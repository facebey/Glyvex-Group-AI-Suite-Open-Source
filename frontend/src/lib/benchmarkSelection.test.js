import { describe, expect, it } from "vitest";
import { groupAllOn, turnGroupOn, turnGroupOff, toggleGroup } from "./benchmarkSelection.js";

describe("groupAllOn", () => {
  it("grupo vacío → false (no hay qué marcar)", () => {
    expect(groupAllOn([], [])).toBe(false);
  });

  it("todos activados → true", () => {
    expect(groupAllOn(["a", "b"], [])).toBe(true);
  });

  it("alguno desactivado → false", () => {
    expect(groupAllOn(["a", "b"], ["b"])).toBe(false);
  });
});

describe("turnGroupOn / turnGroupOff", () => {
  it("on: saca del off los ids del grupo, conserva el resto", () => {
    expect(turnGroupOn(["a", "b"], ["a", "b", "c"])).toEqual(["c"]);
  });

  it("off: agrega los ids del grupo (sin duplicados), conserva el resto", () => {
    expect(turnGroupOff(["a", "b"], ["b", "c"])).toEqual(["b", "c", "a"]);
  });
});

describe("toggleGroup", () => {
  const ids = ["a", "b", "c"];

  it("todo on → off (desmarca el grupo completo)", () => {
    expect(toggleGroup(ids, [])).toEqual(["a", "b", "c"]);
  });

  it("parcial → on (activa el grupo completo)", () => {
    expect(toggleGroup(ids, ["b"])).toEqual([]);
  });

  it("todo off → on (conserva el off de otros grupos)", () => {
    expect(toggleGroup(ids, ["a", "b", "c", "x"])).toEqual(["x"]);
  });

  it("grupo vacío → sin cambios", () => {
    expect(toggleGroup([], ["x"])).toEqual(["x"]);
  });
});
