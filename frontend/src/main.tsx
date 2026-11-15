import { createRoot } from "react-dom/client";
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow-condensed/600.css";
import "@fontsource/barlow-condensed/700.css";
import App from "./App";
import "./theme.css";

// No StrictMode: its double-mount races drei <Html> root unmounts under React 19 and drops a label.
createRoot(document.getElementById("root")!).render(<App />);
