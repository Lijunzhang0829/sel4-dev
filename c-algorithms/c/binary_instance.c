/* Verification entry point; upstream algorithms remain in vendor/src. */
#include "compare-int.c"
#include "binary-heap.c"
BinaryHeap *calg_binary_new(void) {
    return binary_heap_new(BINARY_HEAP_TYPE_MIN, int_compare);
}
