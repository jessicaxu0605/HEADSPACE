import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import {
  clearAccessToken,
  getAccessToken,
  getCurrentSession,
  logout,
  setAccessToken,
  subscribeAccessToken,
  type LoginResponse,
  type UserProfile,
} from "../api";

export type SessionState =
  | { status: "checking" }
  | { status: "signed-out" }
  | { status: "signed-in"; user: UserProfile };

type SessionContextValue = SessionState & {
  signIn: (login: LoginResponse) => void;
  signOut: () => void;
};

// The user confirmed for one specific token. A different token (another login,
// another tab) has to be confirmed again before anything is shown.
type VerifiedSession = { token: string; user: UserProfile };

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const token = useSyncExternalStore(subscribeAccessToken, getAccessToken);
  const [verified, setVerified] = useState<VerifiedSession | null>(null);
  const isVerified = token !== null && verified?.token === token;

  useEffect(() => {
    if (token === null || isVerified) return;
    const request = new AbortController();
    getCurrentSession({ signal: request.signal })
      .then((session) => setVerified({ token, user: session.user }))
      .catch(() => {
        if (request.signal.aborted) return;
        // A 401 has already cleared the token; any other failure leaves the
        // stored token unusable, so start over at sign-in.
        if (getAccessToken() === token) clearAccessToken();
      });
    return () => request.abort();
  }, [isVerified, token]);

  const signIn = useCallback((login: LoginResponse): void => {
    setVerified({ token: login.access_token, user: login.session.user });
    setAccessToken(login.access_token);
  }, []);

  const signOut = useCallback((): void => {
    // Revoke the server session in the background; the reader is signed out
    // locally either way.
    void logout().catch(() => undefined);
    clearAccessToken();
    setVerified(null);
  }, []);

  const value = useMemo<SessionContextValue>(() => {
    const state: SessionState =
      token === null
        ? { status: "signed-out" }
        : isVerified && verified
          ? { status: "signed-in", user: verified.user }
          : { status: "checking" };
    return { ...state, signIn, signOut };
  }, [isVerified, signIn, signOut, token, verified]);

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

export function useSession(): SessionContextValue {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider.");
  return session;
}
