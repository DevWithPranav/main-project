// Write permissions per role, mirroring the Role column of backend/API.md. The backend enforces
// them; the UI only hides what a role cannot do.

import type { Role, TrafficEvent } from '../api/types';

export type Action =
  | 'review_violation'
  | 'review_anomaly'
  | 'import_session'
  | 'edit_profile'
  | 'decide_recommendation'
  | 'edit_zone'
  | 'deactivate_zone';

const RULES: Record<Action, Role[]> = {
  review_violation: ['OFFICER', 'ADMIN'],
  review_anomaly: ['MAINTENANCE', 'ADMIN'], // API.md: anomalies are reviewed by MAINTENANCE / ADMIN
  import_session: ['OPERATOR', 'ADMIN'],
  edit_profile: ['ADMIN', 'PLANNER'],
  decide_recommendation: ['PLANNER', 'ADMIN'],
  edit_zone: ['PLANNER', 'ADMIN'], // POST / PUT /api/zones
  deactivate_zone: ['ADMIN'], // DELETE /api/zones/{id}
};

export function can(role: Role | null | undefined, action: Action): boolean {
  return !!role && RULES[action].includes(role);
}

export function canReview(role: Role | null | undefined, e: Pick<TrafficEvent, 'kind'>): boolean {
  return can(role, e.kind === 'anomaly' ? 'review_anomaly' : 'review_violation');
}

export const ROLE_COLORS: Record<Role, string> = {
  OFFICER: 'blue',
  OPERATOR: 'teal',
  PLANNER: 'grape',
  MAINTENANCE: 'orange',
  ADMIN: 'red',
};
