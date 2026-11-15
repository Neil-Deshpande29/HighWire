import { createRoot } from "react-dom/client";
import "@fontsource-variable/comfortaa"; // one rounded face for everything, text and numbers (weights 300 to 700)
import App from "./App";
import "./theme.css";

// No StrictMode: its double-mount races drei <Html> root unmounts under React 19 and drops a label.
createRoot(document.getElementById("root")!).render(<App />);
