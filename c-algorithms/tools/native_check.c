/* Preparation-only differential checks against a pointer multiset. */
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "binary-heap.h"
#include "compare-int.h"

static void *blocks[16];
static size_t sizes[16];
static unsigned alloc_calls, fail_at, forced_moves, live_count;

static int slot(void *p) {
    for (int i = 0; i < 16; ++i) if (blocks[i] == p) return i;
    abort();
}
static void register_block(void *p, size_t n) {
    assert(p);
    int i = slot(NULL);
    blocks[i] = p; sizes[i] = n; ++live_count;
}
void *audit_malloc(size_t n) {
    if (++alloc_calls == fail_at) return NULL;
    void *p = malloc(n);
    if (p) register_block(p, n);
    return p;
}
void audit_free(void *p) {
    if (!p) return;
    int i = slot(p);
    blocks[i] = NULL; sizes[i] = 0; --live_count;
    free(p);
}
void *audit_realloc(void *p, size_t n) {
    if (++alloc_calls == fail_at) return NULL;
    int i = slot(p);
    void *q;
    if (forced_moves) {
        q = malloc(n);
        if (!q) return NULL;
        memcpy(q, p, sizes[i] < n ? sizes[i] : n);
        free(p);
    } else {
        q = realloc(p, n);
        if (!q) return NULL;
    }
    blocks[i] = q; sizes[i] = n;
    return q;
}

static unsigned rng = 0x51c0ffee;
static unsigned random_word(void) {
    rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
    return rng;
}
static unsigned payload_index(int *p, int *keys) {
    for (unsigned i = 0; i < 32; ++i) if (p == &keys[i]) return i;
    abort();
}

int main(void) {
    /* Both allocation failures during construction must release all heap-owned storage. */
    for (unsigned failure = 1; failure <= 2; ++failure) {
        alloc_calls = 0; fail_at = failure;
        assert(binary_heap_new(BINARY_HEAP_TYPE_MIN, int_compare) == NULL);
        assert(live_count == 0);
    }
    unsigned operations = 0, injected_failures = 2, relocations = 0;
    for (unsigned mode = 0; mode < 2; ++mode) {
        forced_moves = mode; fail_at = 0; alloc_calls = 0;
        BinaryHeap *h = binary_heap_new(BINARY_HEAP_TYPE_MIN, int_compare);
        assert(h && live_count == 2);
        int keys[32]; unsigned bag[32] = {0}; unsigned count = 0;
        for (int i = 0; i < 32; ++i) keys[i] = i % 7 - 3;
        keys[0] = INT_MIN; keys[31] = INT_MAX;
        /* Reach the initial capacity and force realloc failure, then a successful retry. */
        for (int i = 0; i < 16; ++i) {
            assert(binary_heap_insert(h, &keys[i])); ++bag[i]; ++count; ++operations;
        }
        fail_at = alloc_calls + 1;
        assert(!binary_heap_insert(h, &keys[0]));
        assert(binary_heap_num_entries(h) == count);
        ++injected_failures; ++operations; fail_at = 0;
        assert(binary_heap_insert(h, &keys[0])); ++bag[0]; ++count; ++operations;
        relocations += mode;
        for (unsigned step = 0; step < 20000; ++step) {
            unsigned k = random_word() % 32;
            if (count < 1000 && (random_word() % 100 < 53 || count == 0)) {
                assert(binary_heap_insert(h, &keys[k])); ++bag[k]; ++count;
            } else {
                unsigned calls_before = alloc_calls;
                int *p = binary_heap_pop(h);
                assert(alloc_calls == calls_before); /* pop allocates nothing */
                unsigned pos = payload_index(p, keys);
                assert(bag[pos] > 0);
                for (unsigned j = 0; j < 32; ++j) if (bag[j]) assert(*p <= keys[j]);
                --bag[pos]; --count;
            }
            assert(binary_heap_num_entries(h) == count);
            ++operations;
        }
        while (count) {
            int *p = binary_heap_pop(h);
            unsigned pos = payload_index(p, keys);
            assert(bag[pos] > 0);
            for (unsigned j = 0; j < 32; ++j) if (bag[j]) assert(*p <= keys[j]);
            --bag[pos]; --count; ++operations;
        }
        assert(binary_heap_pop(h) == NULL && binary_heap_num_entries(h) == 0);
        binary_heap_free(h);
        assert(live_count == 0);
        for (int i = 1; i < 31; ++i) assert(keys[i] == i % 7 - 3);
        assert(keys[0] == INT_MIN && keys[31] == INT_MAX);
    }
    printf("{\"operations\":%u,\"injected_allocation_failures\":%u,"
           "\"forced_relocation_scenarios\":%u,\"live_blocks_at_end\":%u}\n",
           operations, injected_failures, relocations, live_count);
    return 0;
}
