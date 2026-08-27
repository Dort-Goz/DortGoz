import { describe, expect, test } from "bun:test";
import { PreviewStreamParser } from "../src/lib/livePreview";

const encoder = new TextEncoder();

function part(feed: string, body: number[]): Uint8Array {
  const head = encoder.encode(
    `--dortgozkare\r\nContent-Type: image/jpeg\r\nX-Feed: ${feed}\r\n`
    + `Content-Length: ${body.length}\r\n\r\n`,
  );
  const tail = encoder.encode("\r\n");
  const out = new Uint8Array(head.length + body.length + tail.length);
  out.set(head);
  out.set(body, head.length);
  out.set(tail, head.length + body.length);
  return out;
}

function concat(chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((sum, c) => sum + c.length, 0);
  const out = new Uint8Array(total);
  let at = 0;
  for (const chunk of chunks) { out.set(chunk, at); at += chunk.length; }
  return out;
}

describe("çoklanmış canlı önizleme akışı", () => {
  test("tek kareyi kamera adıyla çözer", () => {
    const frames = new PreviewStreamParser().push(part("kamera1", [1, 2, 3]));

    expect(frames).toHaveLength(1);
    expect(frames[0].feed).toBe("kamera1");
    expect([...frames[0].bytes]).toEqual([1, 2, 3]);
  });

  test("tek okumada gelen çok kamerayı ayırır", () => {
    const frames = new PreviewStreamParser().push(
      concat([part("kamera1", [1]), part("kamera2", [2, 2]), part("kamera3", [3])]),
    );

    expect(frames.map((f) => f.feed)).toEqual(["kamera1", "kamera2", "kamera3"]);
    expect(frames.map((f) => f.bytes.length)).toEqual([1, 2, 1]);
  });

  test("parçalı gelen kare tamamlanana dek tutulur", () => {
    const parser = new PreviewStreamParser();
    const whole = part("kamera1", [9, 9, 9, 9]);

    expect(parser.push(whole.slice(0, 20))).toEqual([]);
    expect(parser.push(whole.slice(20, whole.length - 3))).toEqual([]);
    const frames = parser.push(whole.slice(whole.length - 3));

    expect(frames).toHaveLength(1);
    expect([...frames[0].bytes]).toEqual([9, 9, 9, 9]);
  });

  test("gövdesinde sınır dizisi geçen kare bozulmaz", () => {
    const body = [...encoder.encode("--dortgozkare")];
    const frames = new PreviewStreamParser().push(part("kamera1", body));

    expect(frames).toHaveLength(1);
    expect([...frames[0].bytes]).toEqual(body);
  });

  test("kamera adı olmayan parça atlanır", () => {
    const broken = encoder.encode(
      "--dortgozkare\r\nContent-Type: image/jpeg\r\nContent-Length: 1\r\n\r\n",
    );
    const parser = new PreviewStreamParser();
    parser.push(broken);
    const frames = parser.push(part("kamera1", [7]));

    expect(frames.map((f) => f.feed)).toEqual(["kamera1"]);
  });
});
