import { isTauri, openExternal } from "../lib/tauri.js";

// Enlace a navegador externo. En Tauri la webview no abre <a target="_blank">
// (no hay shell por defecto), así que se intercepta el click y se usa
// shell.open. En modo Tauri NO se pone target="_blank": en WebView2 el
// preventDefault del click no suprime la apertura de nueva ventana de
// target="_blank", y eso abría DOS pestañas por click (una de target="_blank"
// + una de shell.open). Fuera de Tauri (dev) se comporta como un <a> normal.
export default function ExternalLink({ href, children, ...rest }) {
  const tauri = isTauri();
  return (
    <a
      href={href}
      target={tauri ? undefined : "_blank"}
      rel={tauri ? undefined : "noopener noreferrer"}
      onClick={(e) => {
        if (tauri) {
          e.preventDefault();
          openExternal(href);
        }
      }}
      {...rest}
    >
      {children}
    </a>
  );
}
