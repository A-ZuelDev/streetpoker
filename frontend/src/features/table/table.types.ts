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
  'active' | 'seated' | 'folded' | 'all-in' | 'sitting-out';

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

export interface MemberView {
  nickname: string;
  status: string;
  stack: number | null;
  isHost: boolean;
}

export interface SeatRequestView {
  nickname: string;
  seatIndex: number;
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
}

export interface TableDemoView {
  variant: DemoVariant;
  roomName: string;
  roomCode: string;
  smallBlind: number;
  bigBlind: number;
  isHost: boolean;
  street: string;
  pot: number;
  board: readonly CardView[];
  seats: readonly SeatView[];
  legalActions: LegalActionsView | null;
  roomPanel: RoomPanelView;
  chat: readonly ChatMessageView[];
}
