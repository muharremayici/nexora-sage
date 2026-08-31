import { useMutation, useQuery } from '@tanstack/react-query';

export function BrokenMutation() {
  useQuery({ queryKey: ['items'], queryFn: async () => [] });
  const mutation = useMutation({ mutationFn: async () => true });
  return <button onClick={() => mutation.mutate()}>Save</button>;
}
