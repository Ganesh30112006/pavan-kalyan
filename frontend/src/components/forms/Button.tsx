import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Link, type LinkProps } from 'react-router-dom';
import { Spinner } from '../feedback/InlineLoader';
import './forms.css';

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';

interface ButtonOwnProps {
  variant?: ButtonVariant;
  size?: 'md' | 'sm';
  icon?: ReactNode;
  /** Shows a spinner, blocks clicks, and swaps the label for `loadingLabel`. */
  loading?: boolean;
  loadingLabel?: string;
  iconOnly?: boolean;
}

type ButtonProps = ButtonOwnProps & ButtonHTMLAttributes<HTMLButtonElement>;

function classes(variant: ButtonVariant, size: 'md' | 'sm', iconOnly: boolean, loading: boolean, extra?: string) {
  return [
    'button',
    variant !== 'secondary' && `button--${variant}`,
    size === 'sm' && 'button--sm',
    iconOnly && 'button--icon',
    loading && 'button--busy',
    extra,
  ]
    .filter(Boolean)
    .join(' ');
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    icon,
    loading = false,
    loadingLabel,
    iconOnly = false,
    type = 'button',
    disabled,
    children,
    className,
    onClick,
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={classes(variant, size, iconOnly, loading, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      onClick={loading ? undefined : onClick}
      {...rest}
    >
      {loading ? (
        <>
          <span className="button__sizer" aria-hidden="true">
            {icon}
            {children}
          </span>
          <span className="button__busy">
            <Spinner />
            {!iconOnly && <span>{loadingLabel ?? children}</span>}
            {iconOnly && <span className="visually-hidden">{loadingLabel ?? 'Working'}</span>}
          </span>
        </>
      ) : (
        <>
          {icon}
          {children}
        </>
      )}
    </button>
  );
});

type ButtonLinkProps = Omit<ButtonOwnProps, 'loading' | 'loadingLabel'> & LinkProps;

export function ButtonLink({ variant = 'secondary', size = 'md', icon, iconOnly = false, children, className, ...rest }: ButtonLinkProps) {
  return (
    <Link className={classes(variant, size, iconOnly, false, className)} {...rest}>
      {icon}
      {children}
    </Link>
  );
}
