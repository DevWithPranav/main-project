import { Alert, Button, Center, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core';
import { IconRadar } from '@tabler/icons-react';
import { useState } from 'react';
import { MOCK } from '../api';
import { useAuth } from '../auth';

export default function LoginPage() {
  const { login } = useAuth();
  const [user, setUser] = useState('officer');
  const [pw, setPw] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await login(user, pw);
    } catch (x) {
      setErr((x as Error).message || 'Login failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Center h="100vh">
      <Paper withBorder shadow="sm" p="xl" w={360}>
        <form onSubmit={submit}>
          <Stack>
            <Center><IconRadar size={40} /></Center>
            <Title order={3} ta="center">Aerial Traffic Console</Title>
            <TextInput label="Username" value={user} onChange={(e) => setUser(e.currentTarget.value)} required />
            <PasswordInput label="Password" value={pw} onChange={(e) => setPw(e.currentTarget.value)} required />
            {err && <Alert color="red">{err}</Alert>}
            <Button type="submit" loading={busy}>Log in</Button>
            <Text size="xs" c="dimmed">
              Dev users: officer, operator, planner, maintenance, admin (password: name + 123){MOCK ? ' · mock mode' : ''}
            </Text>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
