/** Ícono de red neuronal Glyvex AI — logo del navbar (gradiente violet→sky). */
export default function GlyvexAiIcon({ size = 28 }) {
  return (
    <svg viewBox="0 0 80 80" width={size} height={size} xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="navAiGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#7c3aed" />
          <stop offset="100%" stopColor="#0ea5e9" />
        </linearGradient>
      </defs>
      <circle cx="40" cy="40" r="28" fill="none" stroke="url(#navAiGrad)" strokeWidth="2" />
      <circle cx="40" cy="40" r="8" fill="#7c3aed" />
      <circle cx="40" cy="12" r="4.5" fill="#0ea5e9" />
      <circle cx="64" cy="26" r="4.5" fill="#06b6d4" />
      <circle cx="64" cy="54" r="4.5" fill="#7c3aed" opacity="0.75" />
      <circle cx="40" cy="68" r="4.5" fill="#0ea5e9" opacity="0.75" />
      <circle cx="16" cy="54" r="4.5" fill="#06b6d4" />
      <circle cx="16" cy="26" r="4.5" fill="#7c3aed" opacity="0.55" />
      <line x1="40" y1="32" x2="40" y2="16" stroke="#0ea5e9" strokeWidth="1.5" />
      <line x1="47" y1="35" x2="60" y2="29" stroke="#06b6d4" strokeWidth="1.5" />
      <line x1="47" y1="45" x2="60" y2="51" stroke="#7c3aed" strokeWidth="1.5" />
      <line x1="40" y1="48" x2="40" y2="64" stroke="#0ea5e9" strokeWidth="1.5" />
      <line x1="33" y1="45" x2="20" y2="51" stroke="#06b6d4" strokeWidth="1.5" />
      <line x1="33" y1="35" x2="20" y2="29" stroke="#7c3aed" strokeWidth="1.5" />
    </svg>
  );
}
