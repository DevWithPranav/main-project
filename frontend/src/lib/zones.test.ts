import { can } from './permissions';
import { featureToZone, openRing, polygonArea, polygonProblem } from './zones';

describe('zone helpers', () => {
  const square: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10]];

  it('measures area and drops the closing point', () => {
    expect(polygonArea(square)).toBe(100);
    expect(openRing([...square, [0, 0]])).toEqual(square);
    expect(openRing(square)).toEqual(square);
  });

  it('flags what the backend would refuse', () => {
    expect(polygonProblem(square)).toBeNull();
    expect(polygonProblem(square.slice(0, 2))).toMatch(/3 points/);
    expect(polygonProblem([[0, 0], [10, 10], [10, 0], [0, 10]])).toMatch(/cross/); // bow tie
    expect(polygonProblem([[0, 0], [0.5, 0], [0.5, 0.5]])).toMatch(/under 1/);
  });

  it('turns an API feature into a map zone', () => {
    const z = featureToZone({
      type: 'Feature', id: 'z_1', geometry: { type: 'Polygon', coordinates: [[...square, [0, 0]]] },
      properties: { id: 'z_1', scene: 'Town05', type: 'speed', limit_kmh: 30, active: true, source: 'api', name: 'school' },
    });
    expect(z).toEqual({ id: 'z_1', type: 'speed', name: 'school', source: 'api', polygon: square });
  });

  it('lets planners and admins edit zones, only admins deactivate', () => {
    expect(can('PLANNER', 'edit_zone')).toBe(true);
    expect(can('OFFICER', 'edit_zone')).toBe(false);
    expect(can('PLANNER', 'deactivate_zone')).toBe(false);
    expect(can('ADMIN', 'deactivate_zone')).toBe(true);
  });
});
