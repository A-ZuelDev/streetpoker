# Phase 10E Manual Private Game Test Plan

Use separate browser profiles or private windows when possible so each client has an independent guest session. Keep developer tools available for the privacy and network checks. Record the browser, viewport, room code, and first failing step for any defect.

## Expected Phase 11 limitations

These are expected in Phase 10E and are not failures:

- No automatic reconnect.
- No server-side disconnect grace period.
- No action timers or countdowns.
- No timebanks.
- No automatic timeout actions.

After a network interruption, the table remains visibly stale and all controls remain inert until the user chooses **Reconnect**. Active-hand recovery beyond the current explicit reconnect behavior belongs to Phase 11.

## Checks for every client count

At each stable table state, verify:

- The room name, uppercase room code, blinds, connection state, seat, host status, pot, stacks, and current actor are understandable without opening developer tools.
- **Copy code** copies only the visible room code. Denying clipboard permission leaves the code visible and shows a non-fatal message.
- A command first shows submitting language, then (after an ACK, if observable) waiting-for-table language, and changes the table only after authoritative state arrives.
- Guest IDs, player IDs, guest tokens, passwords, raw server messages, and opponent private cards do not appear in the page, URL, console, storage values intended for display, or accessibility tree.
- Clicking the StreetPoker brand does not leave a live room. Leave and Close are the intentional exit paths.
- Connected, syncing, disconnected, persistence, pending, and error notices never contradict or overlap one another.
- Keyboard focus is visible on buttons, inputs, selects, text areas, the room-panel toggle, and collapsible section controls.
- At 1920×1080, 1920×1200, 1440×900, and 1366×768 there is no horizontal overflow, collision, or unreachable control. Below 1120px the room panel starts closed and can be opened and closed from the keyboard.

## Two-client journey

Use Client A as host and Client B as guest.

### Room setup and seating

1. Client A creates a password-protected room. Expect one create submission, a syncing state, then a quiet connected state after the table snapshot arrives.
2. Copy the room code. Expect the visible code to stay selectable and a brief **Copied** confirmation.
3. Client B first joins with the wrong password. Expect a field-associated, safe password error; no raw server text or password appears as page text.
4. Correct the password and submit again. Expect the old error to disappear when the join succeeds and Client B to enter as an unseated viewer.
5. Client B requests an empty seat. Expect **Requesting seat N…**, no optimistic occupancy, then a visible pending request only after the room update.
6. Client A approves. Expect the request to remain until authoritative state arrives, then both clients show Client B seated. Client B’s pod says **You · Seat N**.
7. Verify only Client A sees host moderation, settings, start, kick, and close controls. An unseated viewer should not see a meaningless Stand control.

### First and second hand

1. Client A selects the primary **Start hand** action. Expect the exact server-projected next hand number to be sent, submitting/waiting feedback, and no client-side eligibility decision based on seat counts.
2. When the hand snapshot arrives, verify only the acting client sees enabled actions and a prominent **Your turn** state. Other clients see who is acting.
3. Exercise fold plus the available check/call and bet/raise actions across successive turns. For wagers, verify the displayed and sent amount is the authoritative total-to value; Min, Max, numeric input, slider, and short all-in (if offered) agree.
4. Before each resulting snapshot, verify pot, stacks, board, contributions, and actor do not change optimistically.
5. On settlement, expect **Hand N complete**, authoritative award rows, **Stacks updated**, and updated seat/member stacks. The previous board is not presented as the current board.
6. Client A sees the primary **Start next hand** action. Client B sees **Waiting for host to start the next hand**.
7. Start the second hand. Expect the exact new authoritative `next_hand_number`; do not infer that it is the previous number plus one.

### Room controls and failure handling

1. Between hands, Client B chooses Stand and confirms the seat clears only after authoritative state.
2. Seat Client B again, then have Client A kick Client B. Expect confirmation, pending feedback, a terminal removal notice for Client B, and no reconnect action for the terminal exit.
3. Rejoin Client B and choose Leave. Expect a room-named terminal notice and no reconnect action.
4. Interrupt Client A’s network or close the test socket. Expect an obvious stale/disconnected state, inert table and room controls, and explicit **Reconnect**. No automatic reconnect should occur.
5. Restore the network and choose Reconnect. Expect connecting, then syncing while stale data remains visible and inert, then connected only after fresh state.
6. Trigger a rejected legal-looking poker action and room action where practical. Expect fixed safe feedback, the unchanged snapshot, controls unlocked appropriately, and a successful retry to supersede the stale error.
7. Client A closes the room. Expect confirmation and pending feedback, followed by a terminal room-closed notice on both clients after authoritative closure; no reconnect action remains.

## Three-client journey

Use Client A as host, Clients B and C as guests.

1. Create and join the room, then have B and C request different seats nearly simultaneously. Expect one request per client, no optimistic seats, and both requests visible to A after state delivery.
2. Approve B and reject C. Expect all three clients to converge: B seated, C unseated, and no stale request/error remains.
3. Turn seating approval off between hands. Have C take an empty seat. Expect the same authoritative request command path but **Take seat** language and no host approval step.
4. Change room name, blinds, starting stack, approval mode, and password only in states where the controls permit them. An unrelated pending Kick/Start/Seat command may disable settings, but must not display **Saving…**. Only a settings command displays saving language.
5. Start a hand and rotate actions across all three seated clients. Cover check/call, bet, raise, and fold when the backend exposes them. Verify actor highlighting and authorized hole cards separately in every client.
6. Complete the hand and start the next hand. Verify award totals and stacks are identical on all clients, while each client’s private-card visibility remains distinct.
7. Have one guest stand and the other leave. Verify the host view and remaining guest view converge only after authoritative updates.

## Six-client journey

Use one host and five guests in six independent tabs/profiles.

1. Join all guests and verify every member is readable in Players / seats without exposing internal identifiers.
2. With approval enabled, request all remaining seats. Approve requests in a different order from arrival. Expect seat numbers and occupant names to remain correct on every client.
3. Repeat setup once with auto-seat mode. Ensure occupied/requested seats cannot be taken twice and all six clients converge.
4. At 1366×768, scroll the RoomPanel through players, controls, and collapsed settings. Ensure destructive controls are separated from poker actions and the table/action bar remain usable.
5. Start a six-player hand. Follow every authoritative actor change and use a mix of fold, check, call, bet, raise, and all-in if the backend naturally offers it. Do not force an illegal state merely to reach all-in.
6. Verify folded/all-in/waiting/acting labels and dealer/SB/BB markers are readable without color alone. Check that every client sees only its own authorized hole cards.
7. Complete settlement, compare every updated stack, verify authoritative awards, and start a second hand.
8. Between hands, test one Stand, one Leave, one Kick, a settings update, and Close. Each operation should show truthful pending feedback and converge across all remaining clients.
9. Interrupt one guest connection during an open room and explicitly reconnect it. Separately close a tab during an active hand and record current Phase 10E behavior without expecting grace, automatic recovery, timers, or timeout actions.

## Completion record

For each 2/3/6-client run, record:

- Pass/fail for setup, seating, hand one, settlement, hand two, controls, reconnect, and privacy.
- Screenshots of connected, pending, disconnected, and hand-complete states.
- Any command sent twice, any optimistic state change, any stale error that survives a successful retry, or any private information visible to the wrong client as a release-blocking defect.
