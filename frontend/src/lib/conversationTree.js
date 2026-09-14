/**
 * conversationTree.js — La conversación como árbol, no como lista.
 *
 * Editar un mensaje y reenviarlo, o regenerar una respuesta, no deberían
 * borrar lo anterior: crean un hermano. Lo que se ve en pantalla es el
 * camino activo desde la raíz, siguiendo el puntero `active_child` de cada
 * nodo.
 *
 * Todo vive en el mismo array `messages` que ya se guarda en la columna JSON
 * de chat_conversations — no hace falta migrar el schema. Para que el nivel
 * raíz tenga dónde guardar su propio puntero (hace falta cuando se edita el
 * primer mensaje de la conversación) se agrega un nodo sintético con
 * id ROOT_ID, que se filtra en todos lados menos en la persistencia.
 *
 * Las conversaciones guardadas antes de esto son arrays planos sin
 * `parent_id`: `toTree` las encadena y quedan como una rama única.
 */

export const ROOT_ID = "__root__";

function uuid() {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** ¿Este array ya está en formato árbol? */
export function isTree(messages) {
  return Array.isArray(messages) && messages.some((m) => m?.id === ROOT_ID);
}

/**
 * Convierte una conversación lineal en árbol de una sola rama.
 * Si ya es un árbol, la devuelve tal cual.
 */
export function toTree(messages) {
  if (!Array.isArray(messages) || messages.length === 0) {
    return [{ id: ROOT_ID, parent_id: null, role: "root", active_child: null }];
  }
  if (isTree(messages)) return messages.map((m) => ({ ...m }));

  const root = { id: ROOT_ID, parent_id: null, role: "root", active_child: null };
  const nodes = [root];
  let parentId = ROOT_ID;

  for (const message of messages) {
    const node = { ...message, id: message.id || uuid(), parent_id: parentId };
    nodes[nodes.length - 1].active_child = node.id;
    nodes.push(node);
    parentId = node.id;
  }
  return nodes;
}

function indexBy(nodes) {
  const map = new Map();
  for (const node of nodes) map.set(node.id, node);
  return map;
}

/** Hijos de un nodo, en orden de creación. */
export function childrenOf(nodes, parentId) {
  return nodes.filter((n) => n.parent_id === parentId);
}

/**
 * Camino activo: los nodos que se muestran, de arriba hacia abajo.
 * No incluye la raíz sintética.
 */
export function getPath(nodes) {
  if (!Array.isArray(nodes) || nodes.length === 0) return [];
  const byId = indexBy(nodes);
  const path = [];

  let current = byId.get(ROOT_ID);
  if (!current) return [];

  // El guard de profundidad protege de un ciclo por datos corruptos: sin él
  // un active_child que apunte hacia atrás colgaría el render.
  let guard = 0;
  while (current && guard < 10_000) {
    guard += 1;
    let nextId = current.active_child;
    let next = nextId ? byId.get(nextId) : null;

    if (!next) {
      // Sin puntero válido se sigue por el último hijo creado, que es el
      // comportamiento correcto para una conversación recién cargada.
      const kids = childrenOf(nodes, current.id);
      next = kids.length > 0 ? kids[kids.length - 1] : null;
    }

    if (!next) break;
    path.push(next);
    current = next;
  }

  return path;
}

/** Posición del nodo entre sus hermanos: {index, total}. 1-based. */
export function siblingInfo(nodes, node) {
  if (!node) return { index: 1, total: 1 };
  const siblings = childrenOf(nodes, node.parent_id);
  const index = siblings.findIndex((n) => n.id === node.id);
  return { index: index >= 0 ? index + 1 : 1, total: siblings.length };
}

/** Devuelve nodos nuevos con el puntero del padre movido a `childId`. */
export function setActiveChild(nodes, parentId, childId) {
  return nodes.map((n) => (n.id === parentId ? { ...n, active_child: childId } : n));
}

/**
 * Mueve la selección de rama de un nodo hacia su hermano anterior o
 * siguiente. `direction` es -1 o 1. Si no hay hermano en esa dirección,
 * devuelve los nodos sin cambios.
 */
export function switchBranch(nodes, node, direction) {
  const siblings = childrenOf(nodes, node.parent_id);
  const current = siblings.findIndex((n) => n.id === node.id);
  const target = current + direction;
  if (current < 0 || target < 0 || target >= siblings.length) return nodes;
  return setActiveChild(nodes, node.parent_id, siblings[target].id);
}

/**
 * Agrega mensajes encadenados bajo `parentId` y los deja activos.
 * Devuelve {nodes, ids} con los ids asignados, en orden.
 */
export function appendChain(nodes, parentId, messages) {
  let next = nodes.map((n) => ({ ...n }));
  const ids = [];
  let currentParent = parentId;

  for (const message of messages) {
    const node = { ...message, id: message.id || uuid(), parent_id: currentParent };
    next = setActiveChild(next, currentParent, node.id);
    next.push(node);
    ids.push(node.id);
    currentParent = node.id;
  }

  return { nodes: next, ids };
}

/** Aplica un patch a un nodo por id. */
export function patchNode(nodes, id, patch) {
  return nodes.map((n) => (n.id === id ? { ...n, ...patch } : n));
}

/** Último nodo del camino activo, o null. */
export function lastOfPath(nodes) {
  const path = getPath(nodes);
  return path.length > 0 ? path[path.length - 1] : null;
}

/** Árbol vacío, listo para una conversación nueva. */
export function emptyTree() {
  return [{ id: ROOT_ID, parent_id: null, role: "root", active_child: null }];
}

/**
 * Nodos que se muestran y se mandan al modelo, sin la raíz sintética ni
 * nada que no sea un turno real.
 */
export function visiblePath(nodes) {
  return getPath(nodes).filter((n) => n.role === "user" || n.role === "assistant");
}
