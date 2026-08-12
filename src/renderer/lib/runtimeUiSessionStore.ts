import { useCallback, useLayoutEffect, useRef, useState } from 'react';

type InitialValue<T> = T | (() => T);
type StateUpdate<T> = T | ((previous: T) => T);

// Pure renderer-session state. It intentionally has no localStorage/SQLite
// backing: navigating between modules keeps the value, while a renderer/app
// restart restores product defaults.
const runtimeUiSessionValues = new Map<string, unknown>();

function materialize<T>(initialValue: InitialValue<T>): T {
  return typeof initialValue === 'function'
    ? (initialValue as () => T)()
    : initialValue;
}

export function readRuntimeUiSessionValue<T>(key: string, initialValue: InitialValue<T>): T {
  if (runtimeUiSessionValues.has(key)) return runtimeUiSessionValues.get(key) as T;
  const value = materialize(initialValue);
  runtimeUiSessionValues.set(key, value);
  return value;
}

export function writeRuntimeUiSessionValue<T>(key: string, value: T): void {
  runtimeUiSessionValues.set(key, value);
}

export function useRuntimeUiSessionState<T>(
  key: string,
  initialValue: InitialValue<T>,
): [T, (update: StateUpdate<T>) => void] {
  const initialRef = useRef(initialValue);
  const keyRef = useRef(key);
  const [value, setValue] = useState<T>(() => readRuntimeUiSessionValue(key, initialRef.current));

  useLayoutEffect(() => {
    if (keyRef.current === key) return;
    keyRef.current = key;
    setValue(readRuntimeUiSessionValue(key, initialRef.current));
  }, [key]);

  const updateValue = useCallback((update: StateUpdate<T>) => {
    setValue((previous) => {
      const next = typeof update === 'function'
        ? (update as (current: T) => T)(previous)
        : update;
      writeRuntimeUiSessionValue(keyRef.current, next);
      return next;
    });
  }, []);

  return [value, updateValue];
}

export function clearRuntimeUiSessionValuesForTest(): void {
  runtimeUiSessionValues.clear();
}

