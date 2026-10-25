import { DEFAULT_WINDOW, missingZones, setRuleWindow, toggleRule } from "../../domain/areaRules";
import { RULE_INFO, RULE_TYPES } from "../../domain/ruleCatalog";
import { SEVERITY_LABELS } from "../../domain/events";
import {
  VIOLATION_LABELS,
  ZONE_LABELS,
  type RuleSchedule,
  type ViolationType,
  type Zone,
} from "../../domain/types";

interface Props {
  value: ViolationType[];
  onChange: (next: ViolationType[]) => void;
  /** зоны камеры: если правилу нужна зона, а её нет, показываем предупреждение */
  zones?: Zone[];
  disabled?: boolean;
  /** окна действия правил; нет onSchedule — расписание не показываем */
  schedule?: RuleSchedule;
  onSchedule?: (next: RuleSchedule) => void;
  /** уникальная часть имён полей: на странице бывает несколько списков */
  name: string;
}

/** Какие нарушения контролировать: чекбокс на каждый тип из реестра. */
export default function RulesChecklist({
  value,
  onChange,
  zones,
  disabled,
  name,
  schedule = {},
  onSchedule,
}: Props) {
  const lacking = zones ? missingZones(value, zones) : [];
  return (
    <fieldset className="rule-list" disabled={disabled}>
      <legend className="sr-only">Контролируемые нарушения</legend>
      {RULE_TYPES.map((type) => {
        const info = RULE_INFO[type];
        const warn = lacking.find((item) => item.type === type);
        return (
          <label key={type} className={`rule-item sev-${info.severity}`}>
            <input
              type="checkbox"
              name={`${name}-${type}`}
              checked={value.includes(type)}
              onChange={() => onChange(toggleRule(value, type))}
            />
            <span className="rule-name">{VIOLATION_LABELS[type]}</span>
            <span className="rule-meta">
              {SEVERITY_LABELS[info.severity]}
              {!info.defaultOn && " · не проверено на площадке"}
              {info.zoneKind && ` · нужна зона «${ZONE_LABELS[info.zoneKind]}»`}
            </span>
            {warn && <span className="rule-warn">на камере нет такой зоны: правило ничего не найдёт</span>}
            {onSchedule && value.includes(type) && (
              <span className="rule-window">
                <label>
                  <input
                    type="checkbox"
                    checked={!!schedule[type]}
                    onChange={(e) => onSchedule(setRuleWindow(schedule, type, e.target.checked ? DEFAULT_WINDOW : null))}
                  />{" "}
                  по расписанию
                </label>
                {schedule[type] && (
                  <>
                    <input
                      type="time"
                      aria-label={`${VIOLATION_LABELS[type]}: с`}
                      value={schedule[type].start}
                      onChange={(e) => onSchedule(setRuleWindow(schedule, type, { ...schedule[type], start: e.target.value }))}
                    />
                    <span>–</span>
                    <input
                      type="time"
                      aria-label={`${VIOLATION_LABELS[type]}: до`}
                      value={schedule[type].end}
                      onChange={(e) => onSchedule(setRuleWindow(schedule, type, { ...schedule[type], end: e.target.value }))}
                    />
                  </>
                )}
              </span>
            )}
          </label>
        );
      })}
    </fieldset>
  );
}
