import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./desk/desk.css";

// No StrictMode: its double-mount races drei <Html> root unmounts under React 19 and drops a label.
createRoot(document.getElementById("root")!).render(<App />);
