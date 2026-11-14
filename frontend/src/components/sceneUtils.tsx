import { Html } from "@react-three/drei";
import { createContext, useContext, type RefObject } from "react";

// All <Html> share one stable portal container. Without it drei re-creates each label's React root when the
// canvas event layer connects, which drops labels and spams unmount warnings under React 19.
export const PortalCtx = createContext<RefObject<HTMLDivElement | null> | null>(null);
export function Label(props: React.ComponentProps<typeof Html>) {
  const portal = useContext(PortalCtx);
  return <Html {...props} portal={portal as React.MutableRefObject<HTMLElement>} />;
}
