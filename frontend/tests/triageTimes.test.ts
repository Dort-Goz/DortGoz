import { expect, test } from "bun:test";
import { parseClock } from "../src/components/TriagePanel";

test("dd:ss biçimini saniyeye çevirir", () => {
  expect(parseClock("00:00")).toBe(0);
  expect(parseClock("01:30")).toBe(90);
  expect(parseClock("12:05")).toBe(725);
  expect(parseClock("120:00")).toBe(7200);
});

test("düz saniye girişini kabul eder", () => {
  expect(parseClock("42")).toBe(42);
  expect(parseClock(" 7 ")).toBe(7);
});

test("boş giriş düzeltme yok demektir", () => {
  expect(parseClock("")).toBeNull();
  expect(parseClock("   ")).toBeNull();
});

test("geçersiz giriş null döner, sıfır değil", () => {
  expect(parseClock("abc")).toBeNull();
  expect(parseClock("1:2:3")).toBeNull();
  expect(parseClock("-5")).toBeNull();
  expect(parseClock("01:-30")).toBeNull();
});

test("ondalık saniye korunur", () => {
  expect(parseClock("00:12.5")).toBe(12.5);
});
