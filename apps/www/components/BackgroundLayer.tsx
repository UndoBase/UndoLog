/**
 * BackgroundLayer: Site-wide background singleton.
 * Renders a single fixed-position background behind all page content.
 * Currently uses Transaction Rings (Concept 3: the chosen direction).
 * Swap the imported component to change the global background.
 */
import BackgroundTransactionRings from "./BackgroundTransactionRings";

export default function BackgroundLayer() {
  return <BackgroundTransactionRings />;
}
