import { describe, expect, test } from "bun:test";
import { boxFromPoints, imagePoint } from "../src/lib/trainingBoxes";

describe("training box geometry", () => {
  test("maps responsive screen coordinates to image pixels and clamps edges", () => {
    const bounds = { left: 100, top: 50, width: 320, height: 180 };
    expect(imagePoint(260, 140, bounds, 640, 360)).toEqual({ x: 320, y: 180 });
    expect(imagePoint(0, 999, bounds, 640, 360)).toEqual({ x: 0, y: 360 });
  });

  test("normalizes reverse drags and rejects accidental clicks", () => {
    expect(boxFromPoints({ x: 80, y: 70 }, { x: 20, y: 10 })).toEqual({
      x: 20,
      y: 10,
      width: 60,
      height: 60,
    });
    expect(boxFromPoints({ x: 10, y: 10 }, { x: 11, y: 30 })).toBeNull();
  });
});
