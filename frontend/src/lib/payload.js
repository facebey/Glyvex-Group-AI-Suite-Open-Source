/**
 * payload.js — Traducción de los mensajes guardados al formato que espera el
 * endpoint OpenAI-compatible.
 */

/**
 * Content del mensaje tal como lo espera el upstream.
 *
 * Con imágenes va el array multimodal, donde las imágenes viajan como
 * {type: "image_ref"} y el backend las expande a base64 leyendo el archivo
 * de disco justo antes de mandarlas (así el base64 nunca toca el estado de
 * React, sessionStorage ni la DB).
 *
 * Sin imágenes se aplana todo a un string: el resultado que ve el modelo es
 * idéntico, pero evita mandarle un array de partes a builds de llama-server
 * o a backends que solo esperan texto plano.
 */
export function toPayloadContent(message) {
  const usable = (message.attachments || []).filter((a) => a.status === "ready");
  if (usable.length === 0) return message.content;

  const textBlocks = usable
    .filter((a) => a.kind !== "image" && a.text)
    .map((a) => `--- archivo: ${a.filename} ---\n${a.text}`);

  const images = usable.filter((a) => a.kind === "image");

  if (images.length === 0) {
    return [message.content, ...textBlocks].filter(Boolean).join("\n\n");
  }

  return [
    { type: "text", text: message.content || "" },
    ...textBlocks.map((text) => ({ type: "text", text })),
    ...images.map((a) => ({ type: "image_ref", id: a.id, filename: a.filename })),
  ];
}

/**
 * Aplana un mensaje del historial a los mensajes que espera el upstream.
 *
 * Una respuesta que usó tools no es un solo mensaje: es el assistant que
 * pidió las tools, los resultados role=tool, y recién después el texto
 * final. Se guardan los tres para que el próximo turno conserve el contexto
 * de lo buscado en vez de repreguntarle al modelo sobre fuentes que ya no ve.
 */
export function toPayloadMessages(message) {
  const exchange = (message.tool_exchange || []).map((m) => ({ ...m }));
  return [...exchange, { role: message.role, content: toPayloadContent(message) }];
}
