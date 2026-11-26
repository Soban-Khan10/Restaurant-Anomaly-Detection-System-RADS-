// Small inline icons (24x24 outline, currentColor) so the dashboard needs no icon library.
import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true" {...props}>
      {children}
    </svg>
  );
}

export const HomeIcon = (p: IconProps) => (
  <Icon {...p}><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /><path d="M10 21v-6h4v6" /></Icon>
);
export const AlertIcon = (p: IconProps) => (
  <Icon {...p}><path d="M12 3 2 20h20L12 3Z" /><path d="M12 10v4" /><path d="M12 17.5v.01" /></Icon>
);
export const CameraIcon = (p: IconProps) => (
  <Icon {...p}><rect x="2.5" y="6.5" width="13" height="11" rx="2" /><path d="m15.5 10.5 6-3.5v10l-6-3.5" /></Icon>
);
export const RulesIcon = (p: IconProps) => (
  <Icon {...p}><path d="M4 6h10M4 12h16M4 18h7" /><circle cx="17" cy="6" r="2" /><circle cx="14" cy="18" r="2" /></Icon>
);
export const ReportIcon = (p: IconProps) => (
  <Icon {...p}><path d="M6 3h9l4 4v14H6z" /><path d="M14 3v5h5" /><path d="M9 17v-3M12 17v-5M15 17v-2" /></Icon>
);
export const UsersIcon = (p: IconProps) => (
  <Icon {...p}><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c.8-3.5 3.3-5.5 6.5-5.5s5.7 2 6.5 5.5" />
    <path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5c1.9.7 3 2.6 3.5 5.5" /></Icon>
);
export const SoundOnIcon = (p: IconProps) => (
  <Icon {...p}><path d="M4 9v6h4l5 4V5L8 9H4Z" /><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12" /></Icon>
);
export const SoundOffIcon = (p: IconProps) => (
  <Icon {...p}><path d="M4 9v6h4l5 4V5L8 9H4Z" /><path d="m17 9.5 5 5M22 9.5l-5 5" /></Icon>
);
export const LogoutIcon = (p: IconProps) => (
  <Icon {...p}><path d="M14 4h5v16h-5" /><path d="M10 8l-4 4 4 4" /><path d="M6 12h10" /></Icon>
);
export const MenuIcon = (p: IconProps) => (
  <Icon {...p}><path d="M4 6h16M4 12h16M4 18h16" /></Icon>
);
export const InfoIcon = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><path d="M12 7.5v.01" /></Icon>
);
export const ChevronLeftIcon = (p: IconProps) => <Icon {...p}><path d="m15 5-7 7 7 7" /></Icon>;
export const ChevronRightIcon = (p: IconProps) => <Icon {...p}><path d="m9 5 7 7-7 7" /></Icon>;
export const ArrowLeftIcon = (p: IconProps) => <Icon {...p}><path d="M19 12H5" /><path d="m11 6-6 6 6 6" /></Icon>;
export const ExpandIcon = (p: IconProps) => (
  <Icon {...p}><path d="M14 4h6v6" /><path d="M10 20H4v-6" /><path d="m20 4-7 7" /><path d="m4 20 7-7" /></Icon>
);
export const ImageOffIcon = (p: IconProps) => (
  <Icon {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="9" cy="10" r="1.5" />
    <path d="m21 16-5-5-8 8" /><path d="m3 3 18 18" /></Icon>
);
export const DownloadIcon = (p: IconProps) => (
  <Icon {...p}><path d="M12 4v11" /><path d="m7 10 5 5 5-5" /><path d="M5 20h14" /></Icon>
);
export const PlusIcon = (p: IconProps) => <Icon {...p}><path d="M12 5v14M5 12h14" /></Icon>;
export const CheckIcon = (p: IconProps) => <Icon {...p}><path d="m5 12.5 4.5 4.5L19 7.5" /></Icon>;
export const PauseIcon = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M10 9v6M14 9v6" /></Icon>
);
export const PlayIcon = (p: IconProps) => (
  <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="m10 8.5 5.5 3.5-5.5 3.5Z" /></Icon>
);
