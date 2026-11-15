/** The Rewire mark: an orange tile with two lines, one of them re-routed across the other. */
export default function Logo({ size = 34 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 100 100" role="img" aria-label="Rewire">
      <rect width="100" height="100" rx="25" fill="#ee6a38" />
      <g fill="none" stroke="#0b0b0b" strokeWidth="8.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 31H27" />
        <path d="M17 69H35L63 31H82" />
        <path d="M40 37L46 45" />
        <path d="M55 59L61 67" />
        <path d="M71 69H82" />
      </g>
    </svg>
  );
}
