import {
  defaultPlanningPeriodKey,
  planningCycleTypeForKey,
  type PlanningCycleType,
} from './planningPeriods';

export interface PlanSplitPeriodSelection {
  cycleType: PlanningCycleType;
  periodKey: string;
}

export function resolvePlanSplitPeriodOnOpen(
  selection: PlanSplitPeriodSelection,
  now = new Date(),
): PlanSplitPeriodSelection {
  const periodKey = selection.periodKey.trim();
  return {
    cycleType: selection.cycleType,
    periodKey: periodKey || defaultPlanningPeriodKey(selection.cycleType, now),
  };
}

export function validatePlanSplitPeriod(selection: PlanSplitPeriodSelection): string {
  const periodKey = selection.periodKey.trim();
  if (!periodKey) return '请填写计划周期';
  if (planningCycleTypeForKey(periodKey) !== selection.cycleType) {
    return '计划周期与周期类型不一致，请重新选择';
  }
  return '';
}
