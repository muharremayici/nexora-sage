import { createContext, useContext, useOptimistic } from 'react';
import { Dialog } from '@headlessui/react';
import { useMutation, useQuery } from '@tanstack/react-query';

const ThemeContext = createContext('light');

export function PatternSurface() {
  const theme = useContext(ThemeContext);
  const query = useQuery({ queryKey: ['items'], queryFn: async () => [] });
  const [optimisticItems, addOptimisticItem] = useOptimistic(query.data ?? []);
  const mutation = useMutation({
    mutationFn: async (item: string) => item,
    onMutate: async (item) => addOptimisticItem(item),
    onError: () => undefined,
  });
  return (
    <ThemeContext.Provider value={theme}>
      <Dialog open={false} onClose={() => undefined}>
        <button onClick={() => mutation.mutate('new')}>{optimisticItems.length}</button>
      </Dialog>
    </ThemeContext.Provider>
  );
}
