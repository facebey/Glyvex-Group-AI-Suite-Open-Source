// Selección de Benchmark (pulido K3): fuente única de verdad = offPrompts
// (ids de prompts individuales desmarcados). Los checkboxes de categoría y el
// checkbox "Todos" son interruptores de grupo sobre esa misma lista.

// Grupo "todo activado" (un grupo vacío nunca cuenta como activado).
export function groupAllOn(groupIds, offPrompts) {
  return groupIds.length > 0 && groupIds.every((id) => !offPrompts.includes(id));
}

// Activar el grupo: saca del "off" los ids del grupo, conserva el resto.
export function turnGroupOn(groupIds, offPrompts) {
  return offPrompts.filter((id) => !groupIds.includes(id));
}

// Desactivar el grupo: agrega los ids del grupo (sin duplicados), conserva el resto.
export function turnGroupOff(groupIds, offPrompts) {
  return [...new Set([...offPrompts, ...groupIds])];
}

// Toggle de grupo: si está todo activado, lo desactiva todo; si no (parcial
// o todo off), lo activa todo.
export function toggleGroup(groupIds, offPrompts) {
  return groupAllOn(groupIds, offPrompts) ? turnGroupOff(groupIds, offPrompts) : turnGroupOn(groupIds, offPrompts);
}
