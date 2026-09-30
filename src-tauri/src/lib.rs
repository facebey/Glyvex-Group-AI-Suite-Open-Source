use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager, RunEvent, State};

const MAX_ATTEMPTS: u32 = 3;
const HEALTH_DEADLINE: Duration = Duration::from_secs(120);
const POLL_INTERVAL: Duration = Duration::from_secs(1);
const RETRY_DELAY: Duration = Duration::from_secs(2);

/// Puerto con el que se lanza el sidecar. Siempre 7981: la webview vive en
/// `http://127.0.0.1:<puerto>` y `localStorage` es POR ORIGEN — si release
/// usaba 0 (puerto aleatorio por el SO), cada arranque era un origen nuevo y
/// el tema/onboarding/cualquier preferencia en localStorage no persistía
/// (PERS-1). Si 7981 está ocupado, `main.py` prueba 7982…7990 y solo a
/// último recudo queda en dinámico. El puerto final lo reporta el backend
/// con el marker `#PORT_ASSIGNED:<puerto>#`.
const SPAWN_PORT: u16 = 7981;

/// Payload del evento "sidecar-state" que la UI suscribe.
#[derive(Clone, serde::Serialize)]
struct SidecarEvent {
    state: String,
    attempt: u32,
    url: Option<String>,
    message: Option<String>,
}

/// Estado del proceso sidecar (glyvex-backend.exe) compartido entre el
/// thread de ciclo de vida, el comando de reintento y el shutdown.
#[derive(Default)]
struct SidecarState {
    child: Mutex<Option<Child>>,
    /// true mientras un thread de ciclo de vida está corriendo (evita
    /// arranques dobles al abrir varias ventanas o reintentos en cadena).
    active: AtomicBool,
    /// Puerto real del backend, reportado por el marker
    /// `#PORT_ASSIGNED:<puerto>#` del stdout del sidecar.
    /// `None` hasta que llega el primer marker.
    port: Mutex<Option<u16>>,
    /// true cuando la última verificación /api/health pasó (y hasta que el
    /// sidecar muere). Es la puerta del redirect del frontend: solo IPC,
    /// sin fetch cross-origin del webview (CORS/mixed-content fuera del
    /// camino crítico).
    healthy: AtomicBool,
}

fn emit_state(
    app: &AppHandle,
    state: &str,
    attempt: u32,
    url: Option<&str>,
    message: Option<String>,
) {
    let _ = app.emit(
        "sidecar-state",
        SidecarEvent {
            state: state.to_string(),
            attempt,
            url: url.map(str::to_string),
            message,
        },
    );
}

fn health_ok(port: u16) -> bool {
    let url = format!("http://127.0.0.1:{port}/api/health");
    let Ok(resp) = ureq::get(&url).timeout(Duration::from_secs(3)).call() else {
        return false;
    };
    let Ok(v) = resp.into_json::<serde_json::Value>() else {
        return false;
    };
    v.get("status").and_then(|s| s.as_str()) == Some("ok")
}

/// Orden de búsqueda del sidecar:
/// 1. `GLYVEX_BACKEND_EXE` (test/debug: apuntar a un bundle ya construido).
/// 2. Solo en debug: el bundle del repo en `backend/dist/glyvex-backend/`
///    (lo que genera `build.ps1`), resuelto desde la ruta del propio exe
///    (target/debug -> src-tauri -> raíz del repo). Va antes que
///    `resource_dir` porque la copia de resources que tauri-cli hace en dev
///    (target/debug) puede romper el layout onedir de PyInstaller
///    (`_internal/` aplastado).
/// 3. `resource_dir()/glyvex-backend/` y `resource_dir()/resources/glyvex-backend/`
///    (release NSIS: el bundler instala cada resource preservando su path
///    relativo a src-tauri — en Windows resource_dir es el dir del exe —,
///    y el bundle vive en `src-tauri/resources/` (junction a backend/dist)).
fn resolve_backend_exe(app: &AppHandle) -> Option<PathBuf> {
    if let Ok(p) = std::env::var("GLYVEX_BACKEND_EXE") {
        let pb = PathBuf::from(p);
        if pb.exists() {
            return Some(pb);
        }
    }
    #[cfg(debug_assertions)]
    {
        if let Ok(exe) = std::env::current_exe() {
            let root = exe
                .parent()
                .and_then(Path::parent) // target
                .and_then(Path::parent) // src-tauri
                .and_then(Path::parent); // raíz del repo
            if let Some(root) = root {
                let cand = root
                    .join("backend")
                    .join("dist")
                    .join("glyvex-backend")
                    .join("glyvex-backend.exe");
                if cand.exists() {
                    return Some(cand);
                }
            }
        }
    }
    if let Ok(rd) = app.path().resource_dir() {
        for sub in ["glyvex-backend", "resources/glyvex-backend"] {
            let cand = rd.join(sub).join("glyvex-backend.exe");
            if cand.exists() {
                return Some(cand);
            }
        }
    }
    None
}

fn spawn_backend(exe: &Path) -> std::io::Result<Child> {
    // GLYVEX_STDOUT_PIPE=1: el hook del bundle deja stdout en la pipe
    // (solo stderr va al log) para que podamos leer el marker de puerto.
    // GLYVEX_NO_BROWSER: la ventana la da la shell, el backend no abre
    // navegador. GLYVEX_HOST fijo: app local, sin autenticación.
    let mut cmd = Command::new(exe);
    cmd.env("GLYVEX_PORT", SPAWN_PORT.to_string())
        .env("GLYVEX_HOST", "127.0.0.1")
        .env("GLYVEX_NO_BROWSER", "1")
        .env("GLYVEX_STDOUT_PIPE", "1")
        // El bootstrap de la shell (tauri://localhost) hace un fetch
        // cross-origin a /api/health antes de redirigir: sin este origen
        // CORS bloquea el fetch y la espera se extiende al fallback.
        .env(
            "GLYVEX_CORS_ORIGINS",
            "https://tauri.localhost,tauri://localhost",
        )
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW).spawn()
    }
    #[cfg(not(windows))]
    {
        cmd.spawn()
    }
}

/// Extrae el puerto de una línea del stdout del sidecar. El backend imprime
/// `#PORT_ASSIGNED:<puerto>#` justo antes de levantar uvicorn.
fn parse_port_marker(line: &str) -> Option<u16> {
    let rest = line.trim().strip_prefix("#PORT_ASSIGNED:")?;
    let digits = rest.split('#').next()?;
    digits.trim().parse().ok()
}

fn kill_sidecar(state: &SidecarState) {
    state.healthy.store(false, Ordering::SeqCst);
    *state.port.lock().unwrap() = None;
    if let Some(mut child) = state.child.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// Arranca el ciclo de vida del sidecar en su propio thread: spawn →
/// poll /api/health → reintento hasta MAX_ATTEMPTS → estado final por
/// evento. Devuelve false si ya hay un ciclo corriendo.
fn start_sidecar(app: AppHandle) -> bool {
    if !app.state::<SidecarState>().active.compare_exchange(
        false,
        true,
        Ordering::SeqCst,
        Ordering::SeqCst,
    )
    .is_ok()
    {
        return false;
    }
    thread::spawn(move || {
        let state = app.state::<SidecarState>();
        let _ = run_lifecycle(&app, &state);
        // Al salir del ciclo (running o error final) libera el flag para
        // que un reintento manual pueda re-arrancar.
        state.active.store(false, Ordering::SeqCst);
    });
    true
}

fn run_lifecycle(app: &AppHandle, state: &SidecarState) -> Result<(), String> {
    let exe = match resolve_backend_exe(app) {
        Some(exe) => exe,
        None => {
            emit_state(
                app,
                "error",
                0,
                None,
                Some("Sidecar no encontrado: backend/dist/glyvex-backend/glyvex-backend.exe".into()),
            );
            return Err("sidecar no encontrado".into());
        }
    };

    let mut last_error = String::new();
    for attempt in 1..=MAX_ATTEMPTS {
        emit_state(
            app,
            "starting",
            attempt,
            None,
            if attempt > 1 { Some("reintentando…".into()) } else { None },
        );

        let mut child = match spawn_backend(&exe) {
            Ok(c) => c,
            Err(e) => {
                last_error = format!("no se pudo lanzar el sidecar: {e}");
                continue;
            }
        };
        // El reader de stdout consume el marker de puerto (y desaloja la
        // pipe, que si no se lee se satura y el sidecar bloquea al escribir
        // logs de uvicorn).
        let stdout = child.stdout.take();
        *state.child.lock().unwrap() = Some(child);
        if let Some(stdout) = stdout {
            let app_clone = app.clone();
            thread::spawn(move || {
                let st = app_clone.state::<SidecarState>();
                for line in BufReader::new(stdout).lines() {
                    let Ok(line) = line else { break };
                    if let Some(port) = parse_port_marker(&line) {
                        let mut guard = st.port.lock().unwrap();
                        if guard.is_none() {
                            *guard = Some(port);
                        }
                    }
                }
            });
        }

        let deadline = Instant::now() + HEALTH_DEADLINE;
        let mut ok = false;
        loop {
            thread::sleep(POLL_INTERVAL);
            // El marker llega antes de que uvicorn levante; sin puerto aún
            // no hay nada que sondear.
            if let Some(port) = *state.port.lock().unwrap() {
                if health_ok(port) {
                    ok = true;
                    break;
                }
            }
            // El proceso murió antes de servir: no seguir esperando.
            let dead = state
                .child
                .lock()
                .unwrap()
                .as_mut()
                .and_then(|c| c.try_wait().ok().flatten())
                .is_some();
            if dead {
                last_error = "el sidecar se cerró antes de responder /api/health".into();
                break;
            }
            if Instant::now() > deadline {
                last_error = "timeout: el backend no reportó su puerto o /api/health no respondió en 120 s".into();
                break;
            }
        }

        if ok {
            // health_ok solo pasa con un puerto conocido: el marker llegó.
            let port = state
                .port
                .lock()
                .unwrap()
                .expect("puerto reportado por el marker antes de 'running'");
            state.healthy.store(true, Ordering::SeqCst);
            let url = format!("http://127.0.0.1:{port}");
            emit_state(app, "running", attempt, Some(&url), None);
            // Heartbeat: el evento "running" es one-shot; una webview que
            // carga la página después del primer emit no lo recibe jamás y
            // el redirect del bootstrap no ocurre. Se reemite cada 5 s
            // mientras el sidecar siga sano (kill_sidecar baja el flag).
            {
                let app_hb = app.clone();
                let attempt_hb = attempt;
                let url_hb = url.clone();
                thread::spawn(move || {
                    while let Some(st) = app_hb.try_state::<SidecarState>() {
                        if !st.healthy.load(Ordering::SeqCst) {
                            break;
                        }
                        thread::sleep(Duration::from_secs(5));
                        if st.healthy.load(Ordering::SeqCst) {
                            emit_state(&app_hb, "running", attempt_hb, Some(&url_hb), None);
                        }
                    }
                });
            }
            return Ok(());
        }

        kill_sidecar(state);
        if attempt == MAX_ATTEMPTS {
            let msg = format!(
                "el backend no arrancó después de {MAX_ATTEMPTS} intentos. {last_error}"
            );
            emit_state(app, "error", attempt, None, Some(msg.clone()));
            return Err(msg);
        }
        thread::sleep(RETRY_DELAY);
    }
    Err(last_error)
}

/// Puerto real del backend (para el redirect del frontend y el overlay).
#[tauri::command]
fn get_backend_port(state: State<'_, SidecarState>) -> Result<u16, String> {
    match *state.port.lock().unwrap() {
        Some(port) => Ok(port),
        None => Err("el backend todavía no reportó su puerto".to_string()),
    }
}

/// T5.2: la shell arrancó con el flag `--provision` (post-install: abre
/// directo a la pantalla de provisión).
#[tauri::command]
fn provision_requested() -> bool {
    std::env::args().any(|arg| arg == "--provision")
}

/// Gate del redirect del bootstrap: solo pasa con /api/health verificado
/// por la shell (ver SidecarState::healthy).
#[tauri::command]
fn sidecar_healthy(state: State<'_, SidecarState>) -> bool {
    state.healthy.load(Ordering::SeqCst)
}

#[tauri::command]
fn sidecar_restart(app: AppHandle) -> Result<(), String> {
    let state = app.state::<SidecarState>();
    kill_sidecar(&state);
    // Si el ciclo anterior ya terminó (error final) el flag está libre y se
    // arranca uno nuevo; si sigue vivo, él mismo detecta la muerte del child
    // y reintenta por su cuenta.
    if !state.active.load(Ordering::SeqCst) {
        start_sidecar(app);
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            app.manage(SidecarState::default());
            start_sidecar(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_backend_port,
            sidecar_restart,
            provision_requested,
            sidecar_healthy
        ])
        .build(tauri::generate_context!())
        .expect("error al construir la app Tauri");
    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            if let Some(state) = app_handle.try_state::<SidecarState>() {
                kill_sidecar(&state);
            }
        }
    });
}
