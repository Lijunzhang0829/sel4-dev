(* Transactional batches over an audited prefix. Only discarded sketch trials
   may execute the exact number of harness-authored helper admissions. *)
local
  val root = Path.explode "/worker";
  fun path s = Path.append root (Path.basic s);
  fun read s = File.read (path s);
  fun write s text = File.write (path s) text;
  fun publish name text =
    (write (name ^ ".tmp") text;
     OS.FileSys.rename {old=Path.implode (path (name ^ ".tmp")),
                        new=Path.implode (path name)});
  fun element name text = XML.Elem ((name, []), [XML.Text text]);
  val imports = split_lines (read "imports");
  val theory_name = read "theory_name";
  val worker_id = read "worker_id";
  val parents = map Thy_Info.get_theory imports;
  val thy = Theory.begin_theory (theory_name, Position.none) parents;
  val baseline = map #1 (Thm.all_axioms_of thy);
  val active = Unsynchronized.ref (SOME "startup" : string option);
  val completed = Unsynchronized.ref ([] : (string * string) list);
  val stage = Unsynchronized.ref "prefix_execute";
  val stage_since = Unsynchronized.ref (Time.now ());
  val phase_times = Unsynchronized.ref ([] : (string * real) list);
  val current_command = Unsynchronized.ref ("", "");
  val reuse_observation = Unsynchronized.ref (NONE : (unit -> XML.tree option) option);
  val reuse_deadline = Unsynchronized.ref (NONE : Time.time option);
  fun publish_reuse_observation () =
    (case (!active, !reuse_observation, !reuse_deadline) of
      (SOME stem, SOME observe, SOME deadline) =>
        let
          val remaining = Time.toReal (Time.- (deadline, Time.now ()));
          val observed = if remaining <= 0.0 then
              SOME (Regen_Reuse_Diagnostics.skipped "budget_exhausted"
                "Candidate budget was exhausted before optional reuse observation")
            else (case Exn.capture
              (Timeout.apply (Time.fromReal (Real.min (0.5, remaining))) observe) () of
                Exn.Res value => value
              | Exn.Exn (Timeout.TIMEOUT _) =>
                  SOME (Regen_Reuse_Diagnostics.skipped "budget_exhausted"
                    "Optional reuse observation reached its bounded local deadline")
              | Exn.Exn exn =>
                  SOME (Regen_Reuse_Diagnostics.skipped "unavailable" (Runtime.exn_message exn)));
        in case observed of NONE => () | SOME value =>
          write (stem ^ ".reuse-diagnostics.xml") (XML.string_of value) end
    | _ => ());
  fun progress () = (case !active of NONE => () | SOME stem =>
    let val elapsed = Time.toReal (Time.- (Time.now (), !stage_since));
        val times = AList.map_default (op =) (!stage, 0.0) (fn x => x + elapsed) (!phase_times);
        val (command, line) = !current_command;
        val (last, last_line) = (case !completed of x :: _ => x | [] => ("", ""));
        val timing = map (fn (name, seconds) => XML.Elem (("phase",
          [("name", name), ("seconds", Real.toString seconds)]), [])) times;
    in publish (stem ^ ".progress.xml") (XML.string_of (XML.Elem (("native_progress",
         [("worker", worker_id), ("request", stem),
          ("token", read (stem ^ ".progress_token")), ("stage", !stage),
          ("command", command), ("line", line),
          ("last_completed_command", last), ("last_completed_line", last_line)]),
          [XML.Elem (("timings", []), timing)]))) end);
  fun set_stage name =
    let val now = Time.now ();
        val elapsed = Time.toReal (Time.- (now, !stage_since));
    in phase_times := AList.map_default (op =) (!stage, 0.0) (fn x => x + elapsed) (!phase_times);
       stage := name; stage_since := now; progress () end;
  val requested_checkpoints = Unsynchronized.ref ([] : (int * string * string) list);
  val captured_checkpoints = Unsynchronized.ref ([] : (string * Toplevel.state * string) list);
  fun load_checkpoints stem =
    (requested_checkpoints := map2 (fn key => fn line =>
       (the (Int.fromString line), key, read (stem ^ ".checkpoint_" ^ key)))
       (split_lines (read (stem ^ ".checkpoint_keys")))
       (split_lines (read (stem ^ ".checkpoint_lines")));
     captured_checkpoints := []);
  fun command_position tr = the_default "" (Option.map Int.toString (Position.line_of (Toplevel.pos_of tr)));
  fun save_state tr st = (case !active of NONE => () | SOME stem =>
    if not (Toplevel.is_proof st) then () else
    let val {context, goal, ...} = Proof.goal (Toplevel.proof_of st);
        fun term t = YXML.content_of (Syntax.string_of_term context t);
        fun element name text = XML.Elem ((name, []), [XML.Text text]);
        val goals = map (element "goal" o term) (Thm.prems_of goal);
        val assumptions = map (element "assumption" o term o Thm.term_of) (Assumption.all_assms_of context);
        val commands = map (fn (name, line) => XML.Elem (("command", [("name",name),("line",line)]), [])) (rev (!completed));
    in write (stem ^ ".proof-state.xml") (XML.string_of
      (XML.Elem (("proof_state", [("origin","before_failed_command"),
        ("command",Toplevel.name_of tr),("line",command_position tr)]),
        [XML.Elem (("goals",[]),goals), XML.Elem (("assumptions",[]),assumptions),
         XML.Elem (("executed_commands",[]),commands)]))) end);
  val preparing_prefix = Unsynchronized.ref true;
  fun run_command allow_admission tr st =
    Toplevel.command_exception allow_admission tr st;
  fun execute_with_admissions admission_count position text state =
    let val theory = Toplevel.theory_of state;
        val admissions_left = Unsynchronized.ref admission_count;
        val transitions = Outer_Syntax.parse_text theory (fn () => theory) position text;
        fun step tr st =
          let val started = Time.now ();
              val _ = (current_command := (Toplevel.name_of tr, command_position tr); progress ());
              val admission = Toplevel.name_of tr = "sorry";
              val _ = if admission andalso !admissions_left <= 0
                then error "Unexpected proof admission outside a trusted sketch helper" else ();
              val command = run_command admission tr;
              val out = Exn.capture
                (if !preparing_prefix then command else Timeout.apply
                  (Time.fromReal (the (Real.fromString (read "command_timeout")))) command) st;
          in writeln ("REGEN_COMMAND " ^ Toplevel.name_of tr ^ " " ^
               LargeInt.toString (Time.toMilliseconds (Time.- (Time.now (), started))));
             (case out of Exn.Res next =>
                (if admission then admissions_left := !admissions_left - 1 else ();
                 completed := (Toplevel.name_of tr, command_position tr) :: !completed;
                 List.app (fn (line, key, text) =>
                   if command_position tr = Int.toString line andalso Toplevel.is_theory next
                   then captured_checkpoints := (key, next, text) :: !captured_checkpoints else ())
                   (!requested_checkpoints))
              | Exn.Exn _ =>
                  (ignore (Exn.capture (fn () => save_state tr st) ());
                   if is_some (!reuse_deadline) andalso Toplevel.is_proof st andalso
                       member (op =) ["proof", "apply", "by"] (Toplevel.name_of tr)
                   then reuse_observation := SOME (fn () =>
                     Regen_Reuse_Diagnostics.snapshot position text tr st)
                   else ()));
             progress (); Exn.release out end;
        val result = fold step transitions state;
        val _ = if !admissions_left = 0 then ()
          else error "Missing trusted sketch helper admission";
    in result end;
  val execute = execute_with_admissions 0;
  fun audit names st =
    let val _ = if Toplevel.is_theory st then () else error "Unclosed proof/context";
        val finished = execute Position.none "end" st;
        val theory = Toplevel.end_theory Position.none finished;
        val _ = if forall (member (op =) baseline) (map #1 (Thm.all_axioms_of theory))
                then () else error "New axioms in batch";
        (* A named locale exports kernel.foo, not the short name foo that was
           visible while its proof ran. Resolve against this generated theory's
           new exports, never by guessing a locale or borrowing a parent fact.
           Prefer Isabelle's normal namespace resolution when it names a new
           export; otherwise require a unique qualified export. *)
        val facts = Global_Theory.facts_of theory;
        val exports = map #1 (Facts.dest_static true (map Global_Theory.facts_of parents) facts);
        fun exported name =
          if member (op =) exports (Facts.intern facts name)
          then Facts.intern facts name
          else (case filter (fn full => Long_Name.base_name full = name) exports of
                  [full] => full
                | [] => error ("Missing generated fact: " ^ quote name)
                | _ => error ("Ambiguous generated fact: " ^ quote name));
        fun fact name =
          let val thms = Global_Theory.get_thms theory (exported name);
              val _ = if null thms then error "Empty audited fact" else ();
              val _ = Thm.consolidate thms;
              val _ = if Thm_Deps.has_skip_proof thms then error "skip_proof dependency" else ();
              val _ = if null (Thm_Deps.all_oracles thms) then () else error "oracle dependency";
          in writeln ("REGEN_BATCH_CLEAN " ^ name) end;
    in List.app fact names end;
  val initial_text = read "prefix";
  val _ = load_checkpoints "initial";
  val initial = execute (Position.line_file (the (Int.fromString (read "initial_line")))
    ("/candidate/" ^ theory_name ^ ".thy")) initial_text (Toplevel.make_state (SOME thy));
  val _ = set_stage "prefix_audit";
  val _ = audit (split_lines (read "prefix_facts")) initial;
  val _ = preparing_prefix := false;
  val base = Unsynchronized.ref initial;
  val base_text = Unsynchronized.ref initial_text;
  val pending = Unsynchronized.ref (NONE : (Toplevel.state * string) option);
  val checkpoints = Unsynchronized.ref
    ((read "prefix_key", initial, initial_text) :: !captured_checkpoints);
  fun publish_audit stem delta names = publish (stem ^ ".audit.xml")
    (XML.string_of (XML.Elem (("batch_audit", [("version","2"),
      ("worker",worker_id),("request",stem),("theory",theory_name)]),
      [element "prefix" (!base_text), element "delta" delta,
       XML.Elem (("imports",[]), map (element "import") imports),
       XML.Elem (("facts",[]), map (element "fact") names)])));
  val _ = set_stage "ready";
  val _ = write "ready" "ready";
  fun loop n =
    if File.exists (path "stop") then ()
    else if not (File.exists (path (Int.toString n ^ ".request")))
    then (OS.Process.sleep (Time.fromMilliseconds 20); loop n)
    else let
      val stem = Int.toString n;
      val _ = (active := SOME stem; completed := []; current_command := ("", "");
               reuse_observation := NONE; reuse_deadline := NONE;
               stage := "request_received"; stage_since := Time.now (); phase_times := [];
               progress ());
      val _ = if member (op =) ["check", "sketch"] (read (stem ^ ".action")) then
        let val seconds = if File.exists (path (stem ^ ".candidate_timeout"))
            then read (stem ^ ".candidate_timeout") else read "candidate_timeout"
        in reuse_deadline := SOME (Time.+ (Time.now (),
          Time.fromReal (the (Real.fromString seconds)))) end
        else ();
      val _ = set_stage (case read (stem ^ ".action") of
          "check" => "candidate_execute" | "observe" => "observation"
        | "sketch" => "sketch" | "restore" => "restore" | "commit" => "commit"
        | _ => "invalid_request");
      val result = Exn.capture (fn () =>
        if read (stem ^ ".action") = "commit" then
          (case !pending of NONE => error "No pending audited batch"
           | SOME (st, text) =>
               (base := st; base_text := text; pending := NONE;
                checkpoints := filter (fn (_, _, prefix) => String.isPrefix prefix text) (!checkpoints);
                "COMMITTED"))
        else if read (stem ^ ".action") = "restore" then
          (case List.find (fn (key, _, _) => key = read (stem ^ ".checkpoint_key")) (!checkpoints) of
             NONE => error "Unknown audited prefix checkpoint"
           | SOME (_, st, text) => (base := st; base_text := text; pending := NONE; "RESTORED"))
        else if read (stem ^ ".action") = "observe" then
          let
            (* Start from imports, NOT base or pending. Never publish this
               temporary theory or assign it to either proof state. *)
            val name = read (stem ^ ".observation_name");
            val isolated = Theory.begin_theory (name, Position.none) parents;
            val position = Position.line_file 1 ("/candidate/" ^ name ^ ".thy");
            val _ = Timeout.apply
              (Time.fromReal (the (Real.fromString (read (stem ^ ".observation_timeout")))))
              (fn () =>
                let val finished = execute position (read (stem ^ ".request"))
                      (Toplevel.make_state (SOME isolated));
                    val _ = Toplevel.end_theory Position.none finished;
                in () end) ();
          in "OBSERVED" end
        else if read (stem ^ ".action") = "sketch" then
          let
            val _ = (requested_checkpoints := []; captured_checkpoints := []);
            val trial_timeout = if File.exists (path (stem ^ ".candidate_timeout"))
              then read (stem ^ ".candidate_timeout") else read "candidate_timeout";
            (* Discard the conditional trial, including its admitted facts.
               Never assign base, base_text or pending here. *)
            val position = Position.line_file
              (the (Int.fromString (read (stem ^ ".line"))))
              ("/candidate/" ^ read "theory_name" ^ ".thy");
            val _ = Timeout.apply
              (Time.fromReal (the (Real.fromString trial_timeout)))
              (fn () =>
                let val admitted = split_lines (read (stem ^ ".facts"));
                    val _ = if distinct (op =) admitted = admitted then ()
                      else error "Duplicate trusted sketch helper admission";
                    val trial = execute_with_admissions (length admitted)
                      position (read (stem ^ ".request")) (!base);
                    val _ = if Toplevel.is_theory trial then () else error "Unclosed sketch";
                    val finished = execute Position.none "end" trial;
                    val theory = Toplevel.end_theory Position.none finished;
                    val _ = if forall (member (op =) baseline) (map #1 (Thm.all_axioms_of theory))
                      then () else error "New axioms in sketch";
                in () end) ();
          in "CONDITIONAL" end
        else if read (stem ^ ".action") <> "check" then error "Unknown worker action"
        else let
          val _ = pending := NONE;
          val _ = load_checkpoints stem;
          val position = Position.line_file
            (the (Int.fromString (read (stem ^ ".line"))))
            ("/candidate/" ^ read "theory_name" ^ ".thy");
          val delta = read (stem ^ ".request");
          val names = split_lines (read (stem ^ ".facts"));
          val candidate_timeout = if File.exists (path (stem ^ ".candidate_timeout"))
            then read (stem ^ ".candidate_timeout") else read "candidate_timeout";
          val st = Timeout.apply
            (Time.fromReal (the (Real.fromString candidate_timeout)))
            (fn () =>
              let val next = execute position delta (!base);
                  val _ = set_stage "candidate_audit";
                  val _ = audit names next;
                  val _ = set_stage "audit_publication";
                  val _ = publish_audit stem delta names;
              in next end) ();
          (* Native evidence is complete before the response becomes visible.
             stdout remains diagnostic and may be delivered asynchronously. *)
          val _ = checkpoints := !captured_checkpoints @ !checkpoints;
          val _ = pending := SOME (st, !base_text ^ delta);
        in "GREEN" end) ();
      (* The authoritative result is already captured. Optional diagnostics
         cannot turn a proof rejection into timeout, success, or infrastructure
         failure, and run only within the remaining candidate time allowance. *)
      val _ = (case result of Exn.Res _ => () | Exn.Exn _ =>
        ignore (Exn.capture publish_reuse_observation ()));
      val answer = (case result of Exn.Res text => text
        | Exn.Exn (Timeout.TIMEOUT _) =>
            if read (stem ^ ".action") = "observe" then "TIMEOUT\nObservation exceeded the time budget"
            else "TIMEOUT\nCandidate execution and audit exceeded the time budget"
        | Exn.Exn exn => "RED\n" ^ Runtime.exn_message exn);
      val _ = set_stage "response_publication";
      val _ = publish (stem ^ ".response") answer;
      val _ = set_stage "response_ready";
    in loop (n+1) end;
in val _ = loop 1 end;
