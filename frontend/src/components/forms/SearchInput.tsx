import { Search, X } from 'lucide-react';
import { Button } from './Button';
import './forms.css';

interface SearchInputProps {
  label: string;
  value: string;
  onChange(value: string): void;
  placeholder?: string;
}

export function SearchInput({ label, value, onChange, placeholder }: SearchInputProps) {
  return (
    <div className="search-input" role="search">
      <Search size={16} aria-hidden="true" />
      <input
        type="search"
        className="input"
        aria-label={label}
        placeholder={placeholder ?? label}
        value={value}
        maxLength={200}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && value) {
            e.preventDefault();
            onChange('');
          }
        }}
      />
      {value && (
        <Button className="search-input__clear" variant="ghost" size="sm" iconOnly aria-label="Clear search" onClick={() => onChange('')}>
          <X size={14} aria-hidden="true" />
        </Button>
      )}
    </div>
  );
}
