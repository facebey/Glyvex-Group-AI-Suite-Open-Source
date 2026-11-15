// CHAT-TPL-VIEW: helpers puros del visor de chat template embebido en el
// GGUF. Los flags vienen de la metadata del backend (models.py
// _chat_template_flags), que espeja lo que chat.py lee en runtime: una
// bandera undefined significa "no medido" y aquí no se afirma nada. El
// resaltado del template es solo visual: lo que afirma una capacidad es el
// badge de la bandera, nunca que un término aparezca resaltado.

// Literales que busca el detector del backend: kwargs son case-sensitive
// (variables jinja exactas); las de tools son case-insensitive porque así
// las detecta llama.cpp (common/jinja/caps.cpp).
export const TEMPLATE_FLAG_TERMS = {
  enable_thinking_kwarg: { terms: ["enable_thinking"], ci: false },
  preserve_thinking_kwarg: { terms: ["preserve_thinking"], ci: false },
  reasoning_effort_kwarg: { terms: ["reasoning_effort"], ci: false },
  tools_in_template: { terms: ["tools", "tool_call"], ci: true },
};

export const TEMPLATE_FLAGS = Object.keys(TEMPLATE_FLAG_TERMS);

// true | false | null. null = sin dato medido (metadata ausente o clave que
// no vino): la UI no debe ni afirmar ni negar.
export function flagState(meta, key) {
  const value = meta ? meta[key] : undefined;
  if (value === true) return true;
  if (value === false) return false;
  return null;
}

export function confirmedTemplateFlags(meta) {
  return TEMPLATE_FLAGS.filter((key) => flagState(meta, key) === true);
}

// El template embebido real (string no vacío) o null si el archivo no trae.
export function embeddedTemplate(meta) {
  const template = meta ? meta.chat_template : null;
  return typeof template === "string" && template.length > 0 ? template : null;
}

// Divide el template en segmentos { text, hl } resaltando SOLO los literales
// de banderas confirmadas (true). Banderas false o null no resaltan: un
// término parecido en el texto no prueba nada si el detector no lo confirmó.
export function templateHighlightSegments(template, meta) {
  if (!template) return [];
  const terms = [];
  for (const key of confirmedTemplateFlags(meta)) {
    for (const term of TEMPLATE_FLAG_TERMS[key].terms) {
      terms.push({ term, ci: TEMPLATE_FLAG_TERMS[key].ci });
    }
  }
  if (terms.length === 0) return [{ text: template, hl: false }];

  const lowered = template.toLowerCase();
  const segments = [];
  let i = 0;
  while (i < template.length) {
    let best = null;
    for (const { term, ci } of terms) {
      const hay = ci ? lowered : template;
      const idx = hay.indexOf(term, i);
      if (idx !== -1 && (best === null || idx < best.idx || (idx === best.idx && term.length > best.term.length))) {
        best = { idx, term };
      }
    }
    if (!best) {
      segments.push({ text: template.slice(i), hl: false });
      break;
    }
    if (best.idx > i) segments.push({ text: template.slice(i, best.idx), hl: false });
    segments.push({ text: template.slice(best.idx, best.idx + best.term.length), hl: true });
    i = best.idx + best.term.length;
  }
  return segments.filter((s) => s.text.length > 0);
}
