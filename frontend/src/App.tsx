import {
  ActionIcon,
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
import { lazy, Suspense } from 'react';
import { MOCK } from './api';
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
  const loc = useLocation();
  const nav = useNavigate();
  const { setColorScheme } = useMantineColorScheme();
  const scheme = useComputedColorScheme('light');

  if (loading)
    return (
      <Center h="100vh">
        <Loader />
      </Center>
    );
  if (!me) return <LoginPage />;

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 230, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
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
