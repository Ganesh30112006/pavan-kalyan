import { CircleSlash, Info, XCircle } from 'lucide-react';
import { findingLabel, type CapabilityFinding, type CheckedField } from '../../utils/capabilities';
import { Button } from '../forms/Button';
import './feedback.css';

const ICON = { error: XCircle, adjusted: Info, ignored: CircleSlash };

interface CapabilityFindingsProps {
  findings: CapabilityFinding[];
  /** Offers an "Edit" link per finding (e.g. jump to the wizard step that owns the field). */
  onEdit?(field: CheckedField): void;
}

/** Provider capability warnings, each with a text label so severity never depends on colour. */
export function CapabilityFindings({ findings, onEdit }: CapabilityFindingsProps) {
  if (findings.length === 0) return null;
  return (
    <ul className="findings" aria-label="Provider capability warnings">
      {findings.map((f) => {
        const Icon = ICON[f.severity];
        return (
          <li key={`${f.field}:${f.severity}`} className={`findings__item findings__item--${f.severity}`}>
            <Icon size={16} aria-hidden="true" />
            <span className="findings__text">
              <strong>{findingLabel[f.severity]}:</strong> {f.message}
            </span>
            {onEdit && (
              <Button size="sm" variant="ghost" onClick={() => onEdit(f.field)}>
                Edit
              </Button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
