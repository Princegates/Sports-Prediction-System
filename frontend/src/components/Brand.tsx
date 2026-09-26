import { Link } from "react-router-dom";

interface Props {
  /** Route to link to. Omitted for the non-interactive logo shown inside
   * auth cards, where the surrounding page is already the destination. */
  to?: string;
  onClick?: () => void;
  /** Centers the mark + wordmark as a unit, for placements inside a
   * centered card rather than a left-aligned nav bar. */
  center?: boolean;
  className?: string;
}

/**
 * The "SI" mark plus the "Socca Intelligence" wordmark, used in every nav
 * bar and auth-card header. The wordmark is a gradient built from the
 * current theme's accent colors, so it recolors correctly across light/dark
 * mode and every accent theme with no JS, and its gradient sweeps
 * continuously rather than sitting static.
 */
export function Brand({ to, onClick, center = false, className }: Props) {
  const classes = ["brand", center ? "brand-center" : "", className ?? ""].filter(Boolean).join(" ");
  const inner = (
    <>
      <span className="brand-mark">SI</span>
      <span className="brand-word">Socca Intelligence</span>
    </>
  );

  if (to) {
    return (
      <Link to={to} className={classes} onClick={onClick}>
        {inner}
      </Link>
    );
  }

  return <div className={classes}>{inner}</div>;
}
