"""Authored retrieval diagnostics, not an Isabelle theorem database.

The records are synthetic text fixtures.  Neither the source-class spellings nor
synthetic digests certify them as valid or available Isabelle facts.  Relevance
judgments live only in queries; a ranker must receive records and query text only.
"""
from __future__ import annotations

import hashlib
import json
import random


def build_fixture(noise_records: int = 0, seed: int = 0) -> dict:
    """Build independent diagnostics plus deterministic, heterogeneous distractors.

    These are small mechanism tests with authored judgments, not a sampled
    evaluation set or proof-success benchmark.  In particular, the component
    case judges navigation to a symbol family, rather than theorem applicability.
    """
    if isinstance(noise_records, bool) or not isinstance(noise_records, int) or noise_records < 0:
        raise ValueError('noise_records must be a nonnegative integer')
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError('seed must be an integer')
    records: list[dict] = []
    queries: list[dict] = []

    def fact(key: str, name: str, statement: str, *, source: str = 'frozen_parent') -> str:
        owner = 'Lab.' + name.split('.', 1)[0]
        # Include the authored context identity: identical print strings in
        # unrelated toy environments must not be asserted to be one proposition.
        payload = json.dumps([owner, statement], ensure_ascii=False).encode()
        records.append({
            'record_id': 'synthetic:' + key,
            'canonical_name': name,
            'declaration_name': name,
            'fact_index': 0,
            'exact_statement': statement,
            'proposition_digest': 'synthetic-sha256:' + hashlib.sha256(payload).hexdigest(),
            'owner_theory': owner,
            'owner_session': 'Synthetic_Lab',
            'source_class': source,
            'attributes': [],
        })
        return 'synthetic:' + key

    def query(key: str, category: str, mode: str, text: str, relevant: list[str],
              rationale: str, *, forbidden: list[str] | None = None,
              expected_empty: bool = False) -> None:
        queries.append({
            'id': key, 'category': category, 'mode': mode, 'query': text,
            'relevant_ids': relevant, 'forbidden_ids': forbidden or [],
            'expected_empty': expected_empty, 'judgment': 'synthetic_authored',
            'rationale': rationale,
        })

    # Exact lookup controls: suffix ambiguity is reported rather than guessed.
    full = fact('exact_full', 'Lookup.read_token', 'read_token x = token_value x')
    wrong_namespace = fact('exact_namespace', 'OtherLookup.read_token', 'read_token x = other_value x')
    query('exact_fullname', 'exact_controls', 'exact', 'Lookup.read_token', [full],
          'A qualified lookup must select only this namespace.', forbidden=[wrong_namespace])
    query('exact_shortname_ambiguity', 'exact_controls', 'exact', 'read_token',
          [full, wrong_namespace], 'Both matching namespaces are legitimate exact-name results.')
    query('exact_missing', 'exact_controls', 'exact', 'Lookup.absent_token', [],
          'A missing exact name must stay empty rather than being filled with fuzzy results.',
          expected_empty=True)

    underscored = fact('exact_underscore', 'Spell.read_marker', 'read_marker x = x')
    wildcard = fact('exact_wildcard', 'Spell.readXmarker', 'readXmarker x = x')
    query('exact_literal_underscore', 'spelling_controls', 'exact', 'read_marker', [underscored],
          'An underscore is a literal identifier character, not a SQL wildcard.', forbidden=[wildcard])
    prime = fact('search_prime', "Spell.obj_ptr'_def", "obj_ptr' p = p")
    plain = fact('search_plain', 'Spell.obj_ptr_def', 'obj_ptr p = p')
    query('search_prime_distinction', 'spelling_controls', 'search', "obj_ptr'", [prime],
          'Prime and unprimed operation names must remain distinct in explicit search.', forbidden=[plain])
    snake = fact('search_snake', 'Spell.fetch_cell_def', 'fetch_cell p = p')
    camel = fact('search_camel', 'Spell.fetchCell_def', 'fetchCell p = p')
    query('search_spelling_distinction', 'spelling_controls', 'search', 'fetch_cell', [snake],
          'Explicit operation lookup must not silently merge snake_case and camelCase.', forbidden=[camel])

    definition = fact('definition_lhs', 'Definitions.pick_entry_def',
                      'pick_entry p = storage_read p', source='retained_semantic_export')
    consumer = fact('definition_body', 'Definitions.pick_entry_consumer_def',
                    'consumer_entry p = pick_entry p', source='retained_semantic_export')
    query('definition_left_hand_side', 'definition_controls', 'definition', 'pick_entry', [definition],
          'A definition should define the requested constant, not only mention it in another body.',
          forbidden=[consumer])

    # The current name-only gate discards a directly relevant statement.
    body = fact('body_useful', 'Store.balance_after_insert',
                'balanced_store t ==> balanced_store (insert_key k t)')
    misleading_name = fact('name_only', 'Store.balanced_store_measure',
                           'tree_size t = leaf_count t')
    query('body_candidate_suppressed', 'body_candidates', 'search', 'balanced_store', [body],
          'The insert-preservation rule is relevant; a fact with this keyword only in its name '
          'does not establish preservation. Removing the gate should make the body candidate visible.')

    # This is deliberately a lexical navigation judgment, not an equivalence claim.
    component = fact('component_family', 'Navigation.boundary_rule', 'valid_ghost_guard sigma')
    query('goal_component_normalization', 'normalized_fts', 'goal', 'ghost_guard', [component],
          'The authored intent is navigation to the ghost_guard symbol family. The indexed '
          'identifier valid_ghost_guard must expose components consistently with query expansion; '
          'this does not claim ghost_guard and valid_ghost_guard are interchangeable.')

    # Equal propositions under multiple names should not crowd out another rule.
    aliases = [fact(f'alias_{i}', f'Aliases.range_guard_alias_{i:02d}',
                    'range_guard (restrict_range region)') for i in range(8)]
    distinct = fact('alias_distinct', 'Aliases.range_guard_z_extend',
                    'range_guard region ==> range_guard (extend_range region)')
    query('alias_result_diversity', 'digest_groups', 'search', 'range_guard', aliases + [distinct],
          'Two authored proposition groups are useful: restriction and extension. Eight names '
          'for the first group should not occupy the complete first page. Measure group recall, '
          'not the number of alias record IDs retrieved.')

    # This proposed optimization set does not relax explicit-search AND semantics.
    helper = fact('and_helper', 'Chain.walk_chain_invariant', 'walk_chain chain ==> chain_ok chain')
    query('all_terms_still_too_strict', 'known_limitations', 'search', 'walk_chain external_guard', [helper],
          'The invariant is an authored useful helper even though it does not contain the extra '
          'context identifier external_guard. Keeping an all-terms filter still misses it.')

    # A lexical query cannot discriminate among typed instances by itself.
    wrong_types = [fact(f'type_word_{width}', f'Types.increment_identity_A{width:02d}',
                        f'increment_identity (w :: {width} word) = w + 1')
                   for width in (8, 16, 24, 32, 48, 64)]
    nat_instance = fact('type_nat', 'Types.increment_identity_Z_nat',
                        'increment_identity (n :: nat) = n + 1')
    query('type_information_not_in_query', 'known_limitations', 'search', 'increment_identity', [nat_instance],
          'The authored task needs the nat instance, but this query omits that type. All proposed '
          'lexical variants can rank word instances ahead of it. This diagnoses missing target '
          'information, not a claim that word instances are invalid theorems.')

    forward = fact('direction_forward', 'Route.route_ready_z_forward',
                   'route_ready r ==> route_safe r')
    reverse = fact('direction_reverse', 'Route.route_ready_a_reverse',
                   'route_safe r ==> route_ready r')
    query('implication_direction_ambiguity', 'known_limitations', 'search', 'route_ready route_safe', [forward],
          'The authored intended use derives route_safe from route_ready. Reversing implication '
          'preserves the same lexical terms, so lexical ranking alone cannot establish that use.')

    # Continuation is evaluated separately from first-page ranking quality.
    paged = [fact(f'page_{i}', f'Pagination.paged_bridge_{i:02d}',
                  f'paged_bridge branch_{i} = branch_{i}') for i in range(13)]
    query('relevant_fact_on_later_page', 'pagination', 'search', 'paged_bridge', [paged[-1]],
          'The desired branch is deliberately after the first two five-record pages. Continuing '
          'the same result set must eventually expose it without repeating earlier pages.')
    exhausted = [fact(f'exhaust_{i}', f'Exhaust.exhaust_probe_{i}',
                      f'exhaust_probe key_{i} = key_{i}') for i in range(2)]
    query('small_result_exhaustion', 'pagination', 'search', 'exhaust_probe', exhausted,
          'The two-record result set must stop after its first page; offset beyond its end '
          'must remain empty. This does not simulate the separate native definition fallback.')

    goal = fact('goal_full_token', 'Goal.unique_map_key_identity',
                'unique_map_key (unique_map id xs) = xs')
    query('goal_full_identifier_control', 'goal_controls', 'goal',
          'unique_map_key (unique_map id xs) = xs', [goal],
          'A full identifier match should remain retrievable while component indexing changes.')

    long_text = 'long_guard item ==> (' + ' & '.join(f'guard_part_{i} item' for i in range(240)) + ')'
    long_fact = fact('long_statement', 'Long.full_statement', long_text)
    query('complete_statement_control', 'exact_controls', 'exact', 'Long.full_statement', [long_fact],
          'An exact result must preserve the complete statement; result-count budgets do not '
          'authorize truncating a proposition.')

    # Scale is a mixture, not just thousands of completely unrelated records.
    rng = random.Random(seed)
    for index in range(noise_records):
        family = rng.randrange(5)
        suffix = f'{index:07d}'
        salt = rng.randrange(1_000_000)
        if family == 0:
            name = f'Noise.unrelated_{suffix}'
            statement = f'noise_value_{salt} = noise_value_{salt}'
        elif family == 1:
            name = f'Common.map_identity_{suffix}'
            statement = f'map id (xs_{salt} :: nat list) = xs_{salt}'
        elif family == 2:
            name = f'Store.balanced_store_noise_{suffix}'
            statement = f'balanced_store (trim_key key_{salt} tree_{salt})'
        elif family == 3:
            name = f'NoiseAliases.identity_{suffix}'
            statement = 'noise_identity (yy :: nat) = yy'
        else:
            name = f'Common.state_guard_{suffix}'
            statement = f'common_state state_{salt} ==> common_guard state_{salt}'
        fact('noise_' + suffix, name, statement)

    ids = [item['record_id'] for item in records]
    if len(ids) != len(set(ids)):
        raise AssertionError('duplicate fixture record IDs')
    known = set(ids)
    for item in queries:
        if not set(item['relevant_ids'] + item['forbidden_ids']) <= known:
            raise AssertionError('query judgment refers to a missing record')
        if set(item['relevant_ids']) & set(item['forbidden_ids']):
            raise AssertionError('contradictory relevance judgment')
    return {
        'schema': 'premise-retrieval-lab-fixture-v1', 'kind': 'synthetic',
        'seed': seed, 'noise_records': noise_records,
        'records': records, 'queries': queries,
        'notes': [
            'All records and judgments are authored synthetic fixtures; no native Isabelle validation occurred.',
            'Source classes mimic the production input schema only and confer no theorem authority.',
            'All unlisted records are authored distractors for a query, not globally useless facts.',
            'Do not provide relevant_ids, forbidden_ids, rationales, or categories to ranking code.',
            'Report proposition-group recall so aliases do not inflate success counts.',
            'The component test checks lexical navigation; other known-limitation tests expose lexical ambiguity.',
            'Scale noise includes unrelated, common-token, same-family and duplicate-proposition distractors.',
            'This fixture does not establish real-repository accuracy, causal proof improvement, or current import availability.',
        ],
    }
