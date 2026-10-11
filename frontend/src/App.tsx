import {
  ActionIcon,
  Alert,
  AppShell,
  Badge,
  Burger,
  Center,
  Group,
  Loader,
  NavLink,
  Text,
  Tooltip,
  useComputedColorScheme,
  useMantineColorScheme,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import {
  IconAdjustments,
  IconBulb,
  IconChartBar,
  IconCube,
  IconDatabase,
  IconListCheck,
  IconLogout,
  IconMoon,
  IconRadar,
  IconSun,
} from '@tabler/icons-react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { lazy, Suspense, useState } from 'react';
import { api, MOCK } from './api';
import { useAuth } from './auth';
import { ROLE_COLORS } from './lib/permissions';
import ConfigPage from './pages/Config';
import LivePage from './pages/Live';
import LoginPage from './pages/Login';
import RecommendationsPage from './pages/Recommendations';
import SessionsPage from './pages/Sessions';
import StatsPage from './pages/Stats';
import ViolationsPage from './pages/Violations';

// Cesium is large: the twin page loads on first visit only
const TwinPage = lazy(() => import('./pages/Twin'));

const NAV = [
  { to: '/live', label: 'Live monitoring', icon: IconRadar },
  { to: '/violations', label: 'Violations', icon: IconListCheck },
  { to: '/stats', label: 'Statistics', icon: IconChartBar },
  { to: '/sessions', label: 'Sessions', icon: IconDatabase },
  { to: '/config', label: 'Configuration', icon: IconAdjustments },
  { to: '/recommendations', label: 'Recommendations', icon: IconBulb },
  { to: '/twin', label: '3D digital twin', icon: IconCube },
];

export default function App() {
  const { me, loading, logout } = useAuth();
  const [opened, { toggle, close }] = useDisclosure();
  const [deskOpen, setDeskOpen] = useState(() => {
    try { return localStorage.getItem('nav.desktop') !== 'closed'; } catch { return true; }
  });
  const toggleDesk = () => {
    setDeskOpen((v) => {
      try { localStorage.setItem('nav.desktop', v ? 'closed' : 'open'); } catch { /* storage unavailable */ }
      return !v;
    });
  };
  const loc = useLocation();
  const nav = useNavigate();
  const { setColorScheme } = useMantineColorScheme();
  const scheme = useComputedColorScheme('light');
  // PRD 29.2 / 29.5: tell the operator when the database or Redis is down (events are buffered by the pipeline meanwhile)
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 5000, enabled: !!me, retry: false });
  const degraded = health.isError ? 'Backend not reachable' : health.data
    ? [health.data.db !== 'ok' && 'Database degraded — events are being buffered', health.data.redis !== 'ok' && 'Redis down — live updates paused'].filter(Boolean).join('; ')
    : '';

  if (loading)
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    );
  if (!me) return <LoginPage />;

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 230, breakpoint: 'sm', collapsed: { mobile: !opened, desktop: !deskOpen } }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Toggle navigation" />
            <Tooltip label={deskOpen ? 'Hide side bar' : 'Show side bar'}>
              <Burger opened={deskOpen} onClick={toggleDesk} visibleFrom="sm" size="sm" aria-label="Toggle side bar" />
            </Tooltip>
            <IconRadar size={22} />
            <Text fw={700}>Aerial Traffic Console</Text>
            {MOCK && (
              <Badge color="yellow" variant="light">
                mock data
              </Badge>
            )}
          </Group>
          <Group gap="xs">
            <Text size="sm">{me.username}</Text>
            <Badge color={ROLE_COLORS[me.role]} variant="light">
              {me.role}
            </Badge>
            <Tooltip label="Toggle dark mode">
              <ActionIcon variant="subtle" onClick={() => setColorScheme(scheme === 'dark' ? 'light' : 'dark')}>
                {scheme === 'dark' ? <IconSun size={18} /> : <IconMoon size={18} />}
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Log out">
              <ActionIcon variant="subtle" onClick={logout}>
                <IconLogout size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="xs">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            label={n.label}
            leftSection={<n.icon size={18} />}
            active={loc.pathname.startsWith(n.to)}
            onClick={() => {
              nav(n.to);
              close();
            }}
          />
        ))}
      </AppShell.Navbar>
      <AppShell.Main>
        {degraded && !MOCK && (
          <Alert color="red" variant="light" mb="sm" title={degraded}>
            Checked every 5 s; this clears by itself when the service is back.
          </Alert>
        )}
        <Routes>
          <Route path="/live" element={<LivePage />} />
          <Route path="/violations" element={<ViolationsPage />} />
          <Route path="/stats" element={<StatsPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/config" element={<ConfigPage />} />
          <Route path="/recommendations" element={<RecommendationsPage />} />
          <Route
            path="/twin"
            element={
              <Suspense fallback={<Center h={400}><Loader /></Center>}>
                <TwinPage />
              </Suspense>
            }
          />
          <Route path="*" element={<Navigate to="/live" replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}
