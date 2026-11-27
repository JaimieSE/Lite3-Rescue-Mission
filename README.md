# Richard's objective store and mission logic

This package runs **on the robot's onboard Ubuntu computer**, in a terminal opened through Remote Desktop. Your laptop is the screen/keyboard; it does not need ROS installed. It uses Python's built-in SQLite for persistent memory and ROS 2 topics for integration. It never publishes `/cmd_vel`, switches robot modes, starts navigation or arms a drone.

## What is implemented

- Objective records: description, target, optional room/spatial relation, priority, status and distinct entity findings.
- Observation records: saved evidence reference, timestamp, confidence, location and validation state. Room labels and hazards can also be stored as observations.
- QR records: verbatim text and image reference. A trusted parser can create a separate objective referring to the QR. Raw clue text cannot command motion.
- Request records: advisory `explore` or `inspect` requests, with IDs and success/failure/cancellation results.
- Event history: each event is saved atomically; identical retransmissions are ignored. Conflicting reuse of an event ID is rejected.
- Restart persistence, report export, and a ROS-independent CLI for testing.

This is a starting mission-memory layer, not a finished navigation/exploration controller. Request consumers, scene validation, target localisation, room selection, terrain control and drone handoff still need integration. No fixed arena coordinates are included. The sample objectives are **illustrative booklet tasks**, not confirmed competition-day instructions. Edit them before creating a new attempt.

## First test through Remote Desktop

Copy the entire `mission_ws` folder to your onboard **group account's home directory**, for example `~/mission_ws`. Use your group's normal file transfer method. Do not overwrite Jaime's workspace or modify the system computer. Open a terminal inside the Remote Desktop session:

```bash
cd ~/mission_ws/src/rescue_mission
python3 -m unittest discover -s test -v
python3 -m rescue_mission.cli --db /tmp/richard-practice.sqlite3 init config/objectives.json
python3 -m rescue_mission.cli --db /tmp/richard-practice.sqlite3 next
python3 -m rescue_mission.cli --db /tmp/richard-practice.sqlite3 ingest config/example_observation.json
python3 -m rescue_mission.cli --db /tmp/richard-practice.sqlite3 status
python3 -m rescue_mission.cli --db /tmp/richard-practice.sqlite3 export /tmp/practice-report.json
```

Expected: `kitchen_survivor` becomes `complete`; `all_survivors` becomes `active` with one finding. Other objectives remain pending. **The sample image path and coordinates are fictitious. Use this input only with a practice database.** Repeat ingestion to see `accepted: false`. No robot mode change or movement is needed.

## Build and run as ROS 2 nodes

First source the **same ROS distribution and group environment used by Jaime**, in each new terminal. Check the existing environment rather than assuming Humble/Jazzy or changing `ROS_DOMAIN_ID`:

```bash
printenv ROS_DISTRO ROS_DOMAIN_ID
ros2 node list
```

If ROS is not sourced, use your lab's existing setup commands (`source /opt/ros/<installed-distribution>/setup.bash`, then your group's existing workspace setup if needed). Do not type the angle-bracket placeholder literally. Then:

```bash
cd ~/mission_ws
colcon build --symlink-install --packages-select rescue_mission
source install/setup.bash
ros2 run rescue_mission mission_node --ros-args \
  -p db_path:="$HOME/.local/share/rescue_mission/practice-01.sqlite3" \
  -p objectives_path:="$HOME/mission_ws/src/rescue_mission/config/objectives.json"
```

In a second terminal, source the same environments and inspect:

```bash
source ~/mission_ws/install/setup.bash
ros2 topic echo /mission/status
```

The default is store-only: it accepts events and publishes status. To enable **advisory** requests, restart the node with `-p publish_requests:=true` appended. They appear on `/mission/requests`; they do nothing until a separate consumer is connected. Repeated pending requests retain the same ID; consumers must process each ID once. Request publication uses volatile QoS, so old commands are not automatically delivered to newly connected consumers.

Ctrl+C stops this memory node; **it is not a robot stop mechanism**. Disconnecting Remote Desktop is also not a robot stop mechanism. Continue using the group's validated dead-man and operator STOP procedures when movement is integrated.

Use a **new database filename per real attempt**. Restarting with the same filename deliberately resumes existing memory; changing the seed file does not reset existing objectives. Never reuse the practice database for scoring. Run one writer node per database. Treat SQLite as authoritative memory and `/mission/status` as a periodically refreshed view.

## Message contract for Jaime and Keming

### Front-facing color camera on group8

Use ROS domain 8 for both the mission node and the camera adapter, with the
existing camera publisher running. After rebuilding the package, run the adapter
from a terminal with the same ROS setup as the mission node:

```bash
export ROS_DOMAIN_ID=8
ros2 run rescue_mission front_camera
ros2 service call /mission/capture_front_image std_srvs/srv/Trigger '{}'
```

The adapter reads `/builtin_camera/image_raw`, saves JPEG evidence locally, and
adds decoded QR text to the mission store while it is running. Identical QR text
is submitted once per adapter run. The capture service returns the image path
for manual review or a perception adapter. For other targets, submit an
observation referencing that image through the existing event contract. A
reviewed image can support a candidate, and verified completion still requires
a separately validated target position in the map frame.

All topics use `std_msgs/msg/String` containing JSON, following the labs' publisher/subscriber pattern. This avoids custom-message build dependencies for the first integration. Typed messages can replace it later.

| Topic | Producer | Consumer | Purpose |
|---|---|---|---|
| `/mission/events` | Trusted perception/mission/navigation adapters | This node | Input events |
| `/mission/status` | This node | Display, report tools, teammates | Full memory snapshot every second |
| `/mission/requests` | This node, when enabled | Exploration/inspection adapter | Advisory request with correlation ID |
| `/mission/errors` | This node | Developer | Rejected-event explanation |

Every event has this envelope:

```json
{"event_id":"unique-event-id","type":"observation","source":"validated_perception","observed_at":"2026-11-17T12:00:00+08:00","data":{}}
```

`observed_at` is the time the evidence was acquired. Keep the same event ID/content for transport retries; use a new event and observation ID for a new view. IDs for entities must be associated by the perception/localisation adapter: the store deduplicates equal `entity_id` values but does not infer whether two images show the same person.

Supported `data` shapes:

| Type | Required fields / behaviour |
|---|---|
| `observation` | `id`, `entity_id`, `label`, `confidence` in [0,1], `evidence_ref`. Optional `room`, `relation`, `relation_reference`, `warning_symbol`, `location`, `verified`. See sample JSON. |
| `qr` | `id`, `text` (verbatim decoded content), `evidence_ref`. Optional location. Stored without automatically interpreting instructions. |
| `objective` | New `id`, `target`; optional `description`, integer `priority`, `room`, `relation`, `mode: "collection"`, `source_qr_id`. Trusted task parser/operator creates these. |
| `objective_status` | `id`, `status: "pending"` or `"blocked"`, optional `reason`. Use pending to explicitly retry after a failure. Cannot force completion. |
| `search_complete` | Collection objective `id`, `evidence_ref` to a verified search-coverage report. Only the trusted coverage validator/operator should send this. |
| `action_result` | Request `id`, `status: "succeeded"`, `"failed"` or `"cancelled"`, optional `detail`. Failure/cancellation blocks that objective pending explicit recovery. Success does not complete a finding objective. |

Verified observations need confidence >= 0.8, a nonempty evidence reference and finite `x,y,z` in `location.frame_id: "map"`. The threshold is an initial engineering setting, not a competition rule. `verified: true` must come from your evidence validator, **not directly from a VLM's own assertion**. The store checks the record, not the image itself or whether a referenced file exists. Preserve the actual images/bags separately.

Additional completion conditions: medical supplies need an observed room category; electrical hazards need the observed warning symbol; the kitchen survivor needs `room: "kitchen"`, `relation: "right"` and an explicit reference such as the observed entrance. A room classification alone cannot complete a supplies or survivor task. Missing depth/pose leaves a candidate unresolved; the adapter must transform the target position into `map` using sensor calibration and TF. Do not substitute the robot's pose for the target's position.

The collection task stays active after each survivor and needs explicit coverage closure. There is no known fixed survivor count. Blocked tasks remain visible in the report. When no pending/active tasks remain, `review` means a human/integration layer must assess completeness and handoff; it does not declare mission success or launch a drone.

The first selector uses objective priority and available candidates. It does not yet compute route cost, inspect room geometry, retry with backoff, time out requests or automatically finish coverage. An unacknowledged request stays pending, including across restarts. On reconnection, reconcile it with the navigation adapter and send a cancellation if it is stale. Before real movement, add request expiry, current-pose checks and cancellation to that adapter; preserve Jaime's safety supervisor.

## Suggested first integration session

1. Run the local practice example above in the Remote Desktop terminal.
2. Agree exact labels and event IDs with Keming. Feed one real image result as an unverified observation, then a fresh verified view after checking evidence and target location.
3. Ask Jaime for the current ROS topics/actions, map frame and navigation result interface. Connect an adapter that maps advisory requests to his controller and returns matching action results. Do not create a second robot-control stack.
4. Record one clue-to-report trial. Check uncertain evidence, missing location, failure feedback and restart recovery before enabling any motion consumer.

## Worksheet references

- **Week 5 – ROS Lab Exercise 3**, package/node creation and `ament_python`; **Exercise 1–2**, `colcon`, workspace sourcing and publisher/subscriber message types. Used for package structure and topic transport.
- **Week 6 – ROS Lab Exercise 4**, pp. 3–4: shared-robot control, RDP workflow and `lite3_launcher` mapping. This package runs in the onboard group account and does not start or replace that stack. The lab's saved-map exercise is not a competition premapping workflow.
- **Week 8 – ROS Lab Session 6**, scene-understanding structured JSON and evidence; **ROS Lab 6 Part 2**, pp. 20–21: separate perception, mission controller and navigation, with recorded/test inputs. Used for subsystem boundaries.
- **Week 9 – ROS Lab Exercise 7**, TF and object-coordinate exercises: relevant to the future perception-to-map adapter. This package does not fabricate those transforms.
- **Legged Robot Challenge Booklet 2026**, pp. 5, 7, 9: illustrative objectives, observed evidence, online exploration and uncertain/asynchronous perception. These motivate evidence-gated completion and unresolved task memory.
