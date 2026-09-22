import { useState, type FormEvent, type ReactNode } from 'react';

import { formatChips } from '../table/formatChips';
import type { ChatMessageView, RoomPanelView } from '../table/table.types';
import type {
  RoomCommandRequest,
  RoomSettingsPatch,
} from '../../realtime/roomCommands';
import { RoomSettingsEditor } from './RoomSettingsEditor';

interface RoomPanelProps {
  panel: RoomPanelView;
  chat: readonly ChatMessageView[] | null;
  mode: 'demo' | 'live';
  isOpen: boolean;
  onToggle: () => void;
  roomCode?: string;
  onRoomCommand?: (request: RoomCommandRequest) => boolean;
}

interface CollapsibleRoomSectionProps {
  id: string;
  title: string;
  meta?: string;
  className?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

function CollapsibleRoomSection({
  id,
  title,
  meta,
  className = '',
  defaultOpen = true,
  children,
}: CollapsibleRoomSectionProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const contentId = `${id}-content`;

  return (
    <section
      className={`room-panel__section ${className}`.trim()}
      aria-labelledby={`${id}-heading`}
    >
      <div className="room-panel__section-heading">
        <h2 id={`${id}-heading`}>{title}</h2>
        <span className="room-panel__section-heading-meta">
          {meta === undefined ? null : <span>{meta}</span>}
          <button
            type="button"
            aria-expanded={isOpen}
            aria-controls={contentId}
            aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${title} section`}
            onClick={() => setIsOpen((current) => !current)}
          >
            <span className="room-panel__section-chevron" aria-hidden="true">
              {isOpen ? '-' : '+'}
            </span>
          </button>
        </span>
      </div>
      {isOpen ? (
        <div className="room-panel__section-content" id={contentId}>
          {children}
        </div>
      ) : null}
    </section>
  );
}

export function RoomPanel({
  panel,
  chat,
  mode,
  isOpen,
  onToggle,
  roomCode,
  onRoomCommand,
}: RoomPanelProps) {
  const requestCount = panel.seatRequests.length;
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<readonly ChatMessageView[]>(
    chat ?? [],
  );
  const [confirmation, setConfirmation] = useState<
    | { type: 'leave' | 'close_room' }
    | { type: 'kick'; guestId: string; nickname: string }
    | null
  >(null);

  const send = (request: RoomCommandRequest) => {
    if (onRoomCommand?.(request)) {
      setConfirmation(null);
    }
  };

  const saveSettings = (patch: RoomSettingsPatch) =>
    onRoomCommand?.({ type: 'update_settings', patch }) ?? false;

  const submitDemoMessage = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = draft.trim();

    if (message.length === 0) {
      return;
    }

    setMessages((current) => [
      ...current,
      {
        id: `local-demo-${current.length}`,
        sender: 'You',
        message,
        timestamp: 'Now',
      },
    ]);
    setDraft('');
  };

  return (
    <aside
      className={`room-panel room-panel--${isOpen ? 'open' : 'closed'}`}
      aria-label="Room tools"
      aria-busy={
        panel.pendingCommand !== undefined && panel.pendingCommand !== null
      }
    >
      <button
        className="room-panel__toggle"
        type="button"
        aria-expanded={isOpen}
        aria-label={`${isOpen ? 'Collapse' : 'Expand'} room panel`}
        onClick={onToggle}
      >
        <span aria-hidden="true">{isOpen ? 'Hide' : 'Room'}</span>
      </button>

      {isOpen ? (
        <div className="room-panel__body">
          <CollapsibleRoomSection
            id="members"
            title={mode === 'live' ? 'Players / seats' : 'Members'}
            meta={String(panel.members.length)}
          >
            <ul className="member-list" aria-label="Room members">
              {panel.members.map((member) => (
                <li
                  key={member.guestId ?? member.nickname}
                  className="member-row"
                >
                  <span className="member-row__avatar" aria-hidden="true">
                    {member.nickname.slice(0, 1)}
                  </span>
                  <span className="member-row__identity">
                    <span>
                      {member.nickname}
                      {member.isHost ? <small>Host</small> : null}
                    </span>
                    <small>{member.status}</small>
                  </span>
                  <span className="member-row__stack">
                    {member.stack === null ? '-' : formatChips(member.stack)}
                  </span>
                  {mode === 'live' && member.showKick ? (
                    confirmation?.type === 'kick' &&
                    confirmation.guestId === member.guestId ? (
                      <span className="room-panel__confirm">
                        <button
                          className="room-panel__danger"
                          type="button"
                          disabled={!member.canKick}
                          onClick={() =>
                            send({
                              type: 'kick',
                              targetGuestId: confirmation.guestId,
                            })
                          }
                        >
                          Confirm kick {member.nickname}
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmation(null)}
                        >
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        className="member-row__kick"
                        type="button"
                        disabled={!member.canKick}
                        onClick={() =>
                          member.guestId === undefined
                            ? undefined
                            : setConfirmation({
                                type: 'kick',
                                guestId: member.guestId,
                                nickname: member.nickname,
                              })
                        }
                      >
                        Kick {member.nickname}
                      </button>
                    )
                  ) : null}
                </li>
              ))}
            </ul>
          </CollapsibleRoomSection>

          <CollapsibleRoomSection
            key={requestCount === 0 ? 'no-seat-requests' : 'has-seat-requests'}
            id="seat-requests"
            title="Seat requests"
            meta={String(requestCount)}
            defaultOpen={requestCount > 0}
          >
            {requestCount === 0 ? (
              <p className="room-panel__empty">No pending requests</p>
            ) : (
              <ul className="request-list">
                {panel.seatRequests.map((request) => (
                  <li
                    key={
                      request.guestId ??
                      `${request.nickname}-${request.seatIndex}`
                    }
                  >
                    <div>
                      <strong>{request.nickname}</strong>
                      <span>Seat {request.seatIndex + 1}</span>
                    </div>
                    {mode === 'demo' ? (
                      <div className="request-actions">
                        <button type="button" disabled title="Preview only">
                          Approve
                        </button>
                        <button
                          className="request-actions__reject"
                          type="button"
                          disabled
                          title="Preview only"
                        >
                          Reject
                        </button>
                      </div>
                    ) : panel.isHost ? (
                      <div className="request-actions">
                        <button
                          type="button"
                          disabled={!request.canApprove}
                          onClick={() =>
                            request.guestId === undefined
                              ? undefined
                              : send({
                                  type: 'approve_seat',
                                  targetGuestId: request.guestId,
                                })
                          }
                        >
                          Approve
                        </button>
                        <button
                          className="request-actions__reject"
                          type="button"
                          disabled={!request.canReject}
                          onClick={() =>
                            request.guestId === undefined
                              ? undefined
                              : send({
                                  type: 'reject_seat',
                                  targetGuestId: request.guestId,
                                })
                          }
                        >
                          Reject
                        </button>
                      </div>
                    ) : request.isViewer ? (
                      <span className="room-panel__waiting">
                        Waiting for host
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </CollapsibleRoomSection>

          {mode === 'demo' ? (
            <CollapsibleRoomSection
              id="chat"
              title="Chat preview"
              meta="Non-live"
              className="room-panel__chat"
            >
              <ol className="chat-list" aria-label="Recent chat messages">
                {messages.map((chatMessage) => (
                  <li key={chatMessage.id} className="chat-message">
                    <div className="chat-message__meta">
                      <strong>{chatMessage.sender}</strong>
                      <time>{chatMessage.timestamp}</time>
                    </div>
                    <p>{chatMessage.message}</p>
                  </li>
                ))}
              </ol>
              <form className="chat-compose" onSubmit={submitDemoMessage}>
                <label className="sr-only" htmlFor="demo-chat-message">
                  Chat message
                </label>
                <input
                  id="demo-chat-message"
                  type="text"
                  value={draft}
                  placeholder="Say something..."
                  autoComplete="off"
                  onChange={(event) => setDraft(event.currentTarget.value)}
                />
                <button type="submit" disabled={draft.trim().length === 0}>
                  Send
                </button>
              </form>
            </CollapsibleRoomSection>
          ) : null}

          {mode === 'live' ? (
            <CollapsibleRoomSection
              id={panel.isHost ? 'host-controls' : 'player-controls'}
              title={panel.isHost ? 'Host controls' : 'Player controls'}
              defaultOpen
            >
              <div className="host-controls-grid">
                {panel.showStand ? (
                  <button
                    type="button"
                    disabled={!panel.canStand}
                    onClick={() => send({ type: 'stand' })}
                  >
                    Stand
                  </button>
                ) : null}
                {panel.isHost ? (
                  <>
                    <button
                      type="button"
                      disabled={!panel.canStartHand}
                      onClick={() => send({ type: 'start_hand' })}
                    >
                      {panel.startHandLabel ?? 'Start hand'}
                    </button>
                    {panel.isPaused ? (
                      <button
                        type="button"
                        disabled={!panel.canResumeGame}
                        onClick={() => send({ type: 'resume_game' })}
                      >
                        Resume game
                      </button>
                    ) : panel.handInProgress ? (
                      <button
                        type="button"
                        disabled={!panel.canPauseGame}
                        onClick={() => send({ type: 'pause_game' })}
                      >
                        Pause game
                      </button>
                    ) : null}
                    {confirmation?.type === 'close_room' ? (
                      <span className="room-panel__confirm">
                        <button
                          className="room-panel__danger"
                          type="button"
                          disabled={!panel.canCloseRoom}
                          onClick={() => send({ type: 'close_room' })}
                        >
                          Confirm close room
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmation(null)}
                        >
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        className="room-panel__danger"
                        type="button"
                        disabled={!panel.canCloseRoom}
                        onClick={() => setConfirmation({ type: 'close_room' })}
                      >
                        Close room
                      </button>
                    )}
                  </>
                ) : confirmation?.type === 'leave' ? (
                  <span className="room-panel__confirm">
                    <button
                      className="room-panel__danger"
                      type="button"
                      disabled={!panel.canLeave}
                      onClick={() => send({ type: 'leave' })}
                    >
                      Confirm leave room
                    </button>
                    <button type="button" onClick={() => setConfirmation(null)}>
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    className="room-panel__danger"
                    type="button"
                    disabled={!panel.canLeave}
                    onClick={() => setConfirmation({ type: 'leave' })}
                  >
                    Leave room
                  </button>
                )}
              </div>
            </CollapsibleRoomSection>
          ) : null}

          {mode === 'live' && panel.isHost && panel.settings !== undefined ? (
            <CollapsibleRoomSection
              id="room-settings"
              title="Room settings"
              defaultOpen={false}
            >
              <RoomSettingsEditor
                key={JSON.stringify(panel.settings)}
                settings={panel.settings}
                roomCode={roomCode ?? ''}
                handInProgress={panel.handInProgress ?? false}
                standUpActive={panel.standUpActive ?? false}
                disabled={panel.controlsDisabled ?? true}
                savingSettings={panel.pendingCommand?.kind === 'settings'}
                onSave={saveSettings}
              />
            </CollapsibleRoomSection>
          ) : null}

          {mode === 'demo' ? (
            <CollapsibleRoomSection
              id="host-controls"
              title="Host controls"
              className="room-panel__host-controls"
              defaultOpen={false}
            >
              <div className="host-controls-grid">
                <button type="button" disabled title="Demo only">
                  Room settings
                </button>
                <button
                  className="room-panel__danger"
                  type="button"
                  disabled
                  title="Demo only"
                >
                  Close room
                </button>
              </div>
            </CollapsibleRoomSection>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}
