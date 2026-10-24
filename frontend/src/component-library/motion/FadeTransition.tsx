import PresenceTransition, {
  type PresenceTransitionProps,
} from "./PresenceTransition";

export type FadeTransitionProps = Omit<
  PresenceTransitionProps,
  "enter" | "exit"
>;

export default function FadeTransition(props: FadeTransitionProps) {
  return <PresenceTransition {...props} enter="fade" exit="fade" />;
}
