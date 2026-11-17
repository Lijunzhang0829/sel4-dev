/* Verification entry point; original heap implementation is unchanged. */
#include "compare-int.c"
#include "binomial-heap.c"
BinomialHeap *calg_binomial_new(void) {
    return binomial_heap_new(BINOMIAL_HEAP_TYPE_MIN, int_compare);
}
