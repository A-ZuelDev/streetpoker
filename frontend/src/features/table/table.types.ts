export type DemoVariant = 'active' | 'open';

export type SeatPosition =
  | 'bottom-center'
  | 'lower-left'
  | 'upper-left'
  | 'top-center'
  | 'upper-right'
  | 'lower-right';

export type CardRank =
  '2' | '3' | '4' | '5' | '6' | '7' | '8' | '9' | '10' | 'J' | 'Q' | 'K' | 'A';

export type CardSuit = 'clubs' | 'diamonds' | 'hearts' | 'spades';

export interface CardView {
  rank: CardRank;
  suit: CardSuit;
}

export type PlayerState =
  'active' | 'seated' | 'folded' | 'all-in' | 'sitting-out' | 'not-in-hand';

export type BlindMarker = 'small-blind' | 'big-blind';

export interface OccupiedSeatView {
  kind: 'occupied';
  seatIndex: number;
  position: SeatPosition;
  nickname: string;
  stack: number;
  contribution: number | null;
  state: PlayerState;
  isHero: boolean;
  isActing: boolean;
  isDealer: boolean;
  blind: BlindMarker | null;
  cards: readonly CardView[] | 'concealed' | null;
}

export interface EmptySeatView {
  kind: 'empty';
  seatIndex: number;
  position: SeatPosition;
  requestLabel?: string;
  requested?: boolean;
  canRequest?: boolean;
}

export type SeatView = OccupiedSeatView | EmptySeatView;

export interface WagerActionView {
  kind: 'bet' | 'raise';
  minimum: number;
  maximum: number;
  initial: number;
}

export interface LegalActionsView {
  canFold: boolean;
  canCheck: boolean;
  call: { chips: number; total: number } | null;
  wager: WagerActionView | null;
}

export interface LiveActionButtonView<
  Type extends 'fold' | 'check' | 'call' = 'fold' | 'check' | 'call',
> {
  type: Type;
  label: string;
  contextKey: string;
  enabled: boolean;
}

interface LiveWagerBaseView {
  commandType: 'bet_to' | 'raise_to';
  label: 'Bet' | 'Raise';
  contextKey: string;
  minimumFullTo: number;
  maximumTo: number;
  enabled: boolean;
}

export interface LiveWagerRangeView extends LiveWagerBaseView {
  selection: 'range';
  shortAllInTo: null;
  initialTotalTo: number;
}

export interface LiveWagerFixedView extends LiveWagerBaseView {
  selection: 'fixed';
  shortAllInTo: number;
  initialTotalTo: number;
}

export type LiveWagerView = LiveWagerRangeView | LiveWagerFixedView;

export interface LiveActionsView {
  status:
    'your-turn' | 'waiting' | 'watching' | 'syncing' | 'disconnected' | 'open';
  statusLabel: string;
  statusDetail: string;
  protocolWarning: boolean;
  pending: boolean;
  pendingSource: 'poker' | 'room' | null;
  fold: LiveActionButtonView<'fold'> | null;
  middle: LiveActionButtonView<'check' | 'call'> | null;
  wager: LiveWagerView | null;
}

export type RoomPendingKind =
  | 'request-seat'
  | 'approve-seat'
  | 'reject-seat'
  | 'stand'
  | 'leave'
  | 'kick'
  | 'start-hand'
  | 'settings'
  | 'close-room';

export interface RoomPendingView {
  kind: RoomPendingKind;
  phase: 'submitting' | 'waiting';
  label: string;
}

export interface HandCompletionAwardView {
  displayName: string;
  seatNumber: number;
  amount: number;
}

export interface HandCompletionView {
  handNumber: number;
  awards: readonly HandCompletionAwardView[];
}

export interface MemberView {
  guestId?: string;
  nickname: string;
  status: string;
  stack: number | null;
  isHost: boolean;
  isViewer?: boolean;
  showKick?: boolean;
  canKick?: boolean;
}

export interface SeatRequestView {
  guestId?: string;
  nickname: string;
  seatIndex: number;
  isViewer?: boolean;
  canApprove?: boolean;
  canReject?: boolean;
}

export interface RoomSettingsView {
  roomName: string;
  smallBlind: number;
  bigBlind: number;
  defaultStartingStack: number;
  seatingApprovalRequired: boolean;
  maxSeats: number;
  passwordProtected: boolean;
}

export interface ChatMessageView {
  id: string;
  sender: string;
  message: string;
  timestamp: string;
}

export interface RoomPanelView {
  members: readonly MemberView[];
  seatRequests: readonly SeatRequestView[];
  canStartHand: boolean;
  isHost?: boolean;
  canStand?: boolean;
  canLeave?: boolean;
  canCloseRoom?: boolean;
  showStand?: boolean;
  startHandLabel?: 'Start hand' | 'Start next hand';
  pendingCommand?: RoomPendingView | null;
  controlsDisabled?: boolean;
  handInProgress?: boolean;
  settings?: RoomSettingsView;
}

export interface TableView {
  mode: 'demo' | 'live';
  variant: DemoVariant;
  isHandActive: boolean;
  roomName: string;
  roomCode: string;
  smallBlind: number;
  bigBlind: number;
  isHost: boolean;
  street: string;
  actionDeadlineUnixMs: number | null;
  pot: number;
  board: readonly CardView[];
  seats: readonly SeatView[];
  legalActions: LegalActionsView | null;
  liveActions: LiveActionsView | null;
  handCompletion: HandCompletionView | null;
  roomPanel: RoomPanelView;
  chat: readonly ChatMessageView[] | null;
}

export type TableDemoView = TableView;
