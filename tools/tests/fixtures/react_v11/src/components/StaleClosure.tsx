import { useEffect } from 'react';

export function StaleClosure({ value }: { value: string }) {
  useEffect(() => {
    console.log(value);
  }, []);
  return <span>{value}</span>;
}
