import { describe, expect, it } from "vitest";
import {
  ROOT_ID,
  appendChain,
  childrenOf,
  emptyTree,
  getPath,
  switchBranch,
  toTree,
} from "./conversationTree.js";

describe("getPath (E3)", () => {
  it("sigue la rama activa de un array plano encadenado", () => {
    const nodes = toTree([
      { role: "user", content: "hola" },
      { role: "assistant", content: "hola, ¿en qué ayudo?" },
    ]);
    const path = getPath(nodes);
    expect(path.map((n) => n.content)).toEqual(["hola", "hola, ¿en qué ayudo?"]);
  });

  it("sigue el puntero active_child, no al último hijo creado", () => {
    let nodes = toTree([{ role: "user", content: "pregunta" }]);
    const userNode = getPath(nodes)[0];

    let next = appendChain(nodes, userNode.id, [{ role: "assistant", content: "respuesta 1" }]);
    next = appendChain(next.nodes, next.ids[0], [{ role: "user", content: "sigue 1" }]);
    nodes = next.nodes;
    expect(getPath(nodes).at(-1).content).toBe("sigue 1");

    // Regeneración: hermano del assistant bajo el mismo user.
    next = appendChain(nodes, userNode.id, [{ role: "assistant", content: "respuesta 2" }]);
    nodes = next.nodes;
    expect(getPath(nodes).at(-1).content).toBe("respuesta 2");

    // Volver a la rama 1: el puntero vuelve al primer hijo aunque el último
    // creado siga siendo el de la rama 2.
    nodes = switchBranch(nodes, nodes.find((n) => n.content === "respuesta 2"), -1);
    expect(childrenOf(nodes, userNode.id).length).toBe(2);
    expect(getPath(nodes).map((n) => n.content)).toEqual([
      "pregunta",
      "respuesta 1",
      "sigue 1",
    ]);
  });

  it("sin puntero válido cae al último hijo creado", () => {
    const nodes = [
      { id: ROOT_ID, parent_id: null, role: "root", active_child: null },
      { id: "a", parent_id: ROOT_ID, role: "user", content: "a1", active_child: null },
      { id: "b", parent_id: ROOT_ID, role: "user", content: "a2", active_child: null },
    ];
    const path = getPath(nodes);
    expect(path.length).toBe(1);
    expect(path[0].id).toBe("b");
  });

  it("el guard corta un ciclo corrupto sin colgar", () => {
    const nodes = [
      { id: ROOT_ID, parent_id: null, role: "root", active_child: "a" },
      { id: "a", parent_id: ROOT_ID, role: "user", active_child: "b" },
      { id: "b", parent_id: "a", role: "assistant", active_child: "a" }, // ciclo
    ];
    const path = getPath(nodes);
    expect(path.length).toBeGreaterThan(0);
    // El guard corta el bucle al llegar al límite (el camino lo llena todo).
    expect(path.length).toBeLessThanOrEqual(10_000);
  });

  it("devuelve [] para árbol vacío o input inválido", () => {
    expect(getPath([])).toEqual([]);
    expect(getPath(null)).toEqual([]);
    expect(getPath(emptyTree())).toEqual([]);
  });
});
