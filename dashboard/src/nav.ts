// Menu and page access per role (docs/RADS_B..., "Roles (who may do what)"):
//   Overview, incidents, evidence, live view ... everyone
//   Reports ................................... Admin, Manager
//   Cameras & zones, rules .................... Admin, Technical
//   Users ..................................... Admin
import type { ComponentType, SVGProps } from "react";
import { AlertIcon, CameraIcon, HomeIcon, ReportIcon, RulesIcon, UsersIcon } from "./components/Icons";
import type { Role } from "./lib/types";

export interface NavItem {
  to: string;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  roles: Role[];
}

const EVERYONE: Role[] = ["Admin", "Manager", "Technical"];

export const NAV: NavItem[] = [
  { to: "/", label: "Overview", icon: HomeIcon, roles: EVERYONE },
  { to: "/incidents", label: "Incidents", icon: AlertIcon, roles: EVERYONE },
  { to: "/cameras", label: "Cameras & areas", icon: CameraIcon, roles: ["Admin", "Technical"] },
  { to: "/rules", label: "Detection rules", icon: RulesIcon, roles: ["Admin", "Technical"] },
  { to: "/reports", label: "Reports", icon: ReportIcon, roles: ["Admin", "Manager"] },
  { to: "/users", label: "Users", icon: UsersIcon, roles: ["Admin"] },
];

export function navFor(role: Role | undefined): NavItem[] {
  return role ? NAV.filter((n) => n.roles.includes(role)) : [];
}

export function rolesFor(path: string): Role[] {
  return NAV.find((n) => n.to === path)?.roles ?? EVERYONE;
}
