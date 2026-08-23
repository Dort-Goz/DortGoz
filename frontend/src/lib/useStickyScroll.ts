import { useLayoutEffect, useRef } from "react";

export function useStickyScroll<T extends HTMLElement>(dep: unknown) {
  const ref = useRef<T | null>(null);
  const stick = useRef(true);
  const onScroll = () => {
    const el = ref.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [dep]);
  return { ref, onScroll };
}
