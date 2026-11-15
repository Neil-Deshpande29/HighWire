export type NavCmd = "in" | "out" | "left" | "right" | "up" | "down" | "home";

/** Buttons for moving around the 3D terrain, for anyone who would rather not learn the mouse gestures. */
export default function NavPad({ onNav }: { onNav: (c: NavCmd) => void }) {
  const b = (cmd: NavCmd, mark: string, label: string) => <button onClick={() => onNav(cmd)} aria-label={label} title={label}>{mark}</button>;
  return (
    <div className="navpad" role="group" aria-label="Move around the map">
      {b("in", "+", "Zoom in")}{b("out", "−", "Zoom out")}
      <span className="navpad-gap" />
      {b("left", "↺", "Turn left")}{b("right", "↻", "Turn right")}
      <span className="navpad-gap" />
      {b("up", "⌃", "Look from above")}{b("down", "⌄", "Look from the side")}
      <span className="navpad-gap" />
      {b("home", "⌂", "Back to the whole region")}
      <span className="navpad-hint">Drag to move · right-drag to turn · scroll to zoom</span>
    </div>
  );
}
