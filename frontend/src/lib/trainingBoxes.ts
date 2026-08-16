export interface ImagePoint {
  x: number;
  y: number;
}

export interface BoxGeometry {
  x: number;
  y: number;
  width: number;
  height: number;
}

const clamp = (value: number, minimum: number, maximum: number) =>
  Math.min(maximum, Math.max(minimum, value));

export function imagePoint(
  clientX: number,
  clientY: number,
  bounds: { left: number; top: number; width: number; height: number },
  imageWidth: number,
  imageHeight: number,
): ImagePoint {
  return {
    x: clamp(((clientX - bounds.left) / bounds.width) * imageWidth, 0, imageWidth),
    y: clamp(((clientY - bounds.top) / bounds.height) * imageHeight, 0, imageHeight),
  };
}

export function boxFromPoints(
  start: ImagePoint,
  end: ImagePoint,
  minimumSize = 2,
): BoxGeometry | null {
  const x = Math.min(start.x, end.x);
  const y = Math.min(start.y, end.y);
  const width = Math.abs(end.x - start.x);
  const height = Math.abs(end.y - start.y);
  if (width < minimumSize || height < minimumSize) return null;
  return { x, y, width, height };
}
