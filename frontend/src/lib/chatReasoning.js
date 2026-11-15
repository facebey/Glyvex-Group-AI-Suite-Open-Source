// Regla única de CHAT-REASON-CAP: cuándo el effort está comprobablemente
// muerto. El backend marca reasoning_effort=false cuando el chat_template del
// modelo no lee la variable (llama.cpp la analiza en /props); en ese caso ni
// la UI ni el payload deben fingir controlar la profundidad con effort: el
// control real es el presupuesto de thinking, que viaja como
// reasoning_budget_tokens. Sin capacidades medidas la respuesta es "no
// afirmar": la UI no grisea a ciegas ni se inventa soportes.

export function effortUnsupported(capabilities) {
  return Boolean(capabilities) && capabilities.reasoning_effort === false;
}
