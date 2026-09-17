(* Fixed-prefix speculative checker. GREEN still requires the cold checker. *)
local
  val root = Path.explode "/worker";
  fun path s = Path.append root (Path.basic s);
  fun read s = File.read (path s);
  fun write s text = File.write (path s) text;
  val parents = map Thy_Info.get_theory (split_lines (read "imports"));
  val thy = Theory.begin_theory (read "theory_name", Position.none) parents;
  fun execute bounded position text state =
    let
      val theory = Toplevel.theory_of state;
      val transitions = Outer_Syntax.parse_text theory (fn () => theory) position text;
      fun step tr st =
        let val start = Time.now ();
            val action = Toplevel.command_exception true tr;
            val outcome = Exn.capture (if bounded then Timeout.apply (Time.fromReal (the (Real.fromString (read "command_timeout")))) action else action) st;
            val elapsed = Time.toMilliseconds (Time.- (Time.now (), start));
        in writeln ("REGEN_COMMAND " ^ Toplevel.name_of tr ^ " " ^ LargeInt.toString elapsed); Exn.release outcome end;
    in fold step transitions state end;
  val prefix = execute false Position.none (read "prefix") (Toplevel.make_state (SOME thy));
  val _ = write "ready" "ready";
  fun loop n =
    if File.exists (path "stop") then ()
    else if not (File.exists (path (Int.toString n ^ ".request")))
    then (OS.Process.sleep (Time.fromMilliseconds 20); loop n)
    else
      let
        val stem = Int.toString n;
        val result = Exn.capture (fn () =>
          let val position = Position.line_file (the (Int.fromString (read "line_number"))) ("/candidate/" ^ read "theory_name" ^ ".thy");
              val st = execute true position (read (stem ^ ".request")) prefix;
              val _ = if Toplevel.is_theory st then () else error "Unclosed proof/context";
              val finished = execute true Position.none "end" st;
              val thy' = Toplevel.end_theory Position.none finished;
              val facts = Global_Theory.get_thms thy' (read "fact_name");
              val _ = Thm.consolidate facts;
          in "GREEN" end) ();
        val answer = (case result of Exn.Res text => text
          | Exn.Exn exn => "RED\n" ^ Runtime.exn_message exn);
        val _ = write (stem ^ ".response.tmp") answer;
        val _ = OS.FileSys.rename {old = Path.implode (path (stem ^ ".response.tmp")), new = Path.implode (path (stem ^ ".response"))};
      in loop (n + 1) end;
in val _ = loop 1 end;
