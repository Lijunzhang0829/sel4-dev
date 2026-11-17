/* Verification entry point; upstream algorithms remain in vendor/src. */
/*

Copyright (c) 2005-2008, Simon Howard

Permission to use, copy, modify, and/or distribute this software
for any purpose with or without fee is hereby granted, provided
that the above copyright notice and this permission notice appear
in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR
CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

 */
/* Comparison functions for a pointer to an integer */
/*

Copyright (c) 2005-2008, Simon Howard

Permission to use, copy, modify, and/or distribute this software
for any purpose with or without fee is hereby granted, provided
that the above copyright notice and this permission notice appear
in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR
CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

 */
/**
 * @file compare-int.h
 *
 * Comparison functions for pointers to integers.
 *
 * To find the difference between two values pointed at, use
 * @ref int_compare.
 *
 * To find if two values pointed at are equal, use @ref int_equal.
 */
/**
 * Compare the integer values pointed at by two pointers to determine
 * if they are equal.
 *
 * @param location1       Pointer to the first value to compare.
 * @param location2       Pointer to the second value to compare.
 * @return                Non-zero if the two values are equal, zero if the
 *                        two values are not equal.
 */
int int_equal(void *location1, void *location2);
/**
 * Compare the integer values pointed at by two pointers.
 *
 * @param location1        Pointer to the first value to compare.
 * @param location2        Pointer to the second value to compare.
 * @return                 A negative value if the first value is less than
 *                         the second value, a positive value if the first
 *                         value is greater than the second value, zero if
 *                         they are equal.
 */
int int_compare(void *location1, void *location2);
int int_equal(void *vlocation1, void *vlocation2)
{
 int *location1;
 int *location2;
 location1 = (int *) vlocation1;
 location2 = (int *) vlocation2;
 return *location1 == *location2;
}
int int_compare(void *vlocation1, void *vlocation2)
{
 int *location1;
 int *location2;
 location1 = (int *) vlocation1;
 location2 = (int *) vlocation2;
 if (*location1 < *location2) {
  return -1;
 } else if (*location1 > *location2) {
  return 1;
 } else {
  return 0;
 }
}
/*

Copyright (c) 2005-2008, Simon Howard

Permission to use, copy, modify, and/or distribute this software
for any purpose with or without fee is hereby granted, provided
that the above copyright notice and this permission notice appear
in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR
CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

 */
typedef unsigned long size_t;
void *malloc(size_t size);
void *realloc(void *ptr, size_t size);
void free(void *ptr);
/*

Copyright (c) 2005-2008, Simon Howard

Permission to use, copy, modify, and/or distribute this software
for any purpose with or without fee is hereby granted, provided
that the above copyright notice and this permission notice appear
in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR
CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

 */
/**
 * @file binary-heap.h
 *
 * @brief Binary heap.
 *
 * A binary heap is a heap data structure implemented using a
 * binary tree.  In a heap, values are ordered by priority.
 *
 * To create a binary heap, use @ref binary_heap_new.  To destroy a
 * binary heap, use @ref binary_heap_free.
 *
 * To insert a value into a binary heap, use @ref binary_heap_insert.
 *
 * To remove the first value from a binary heap, use @ref binary_heap_pop.
 *
 */
/**
 * Heap type.  If a heap is a min heap (@ref BINARY_HEAP_TYPE_MIN), the
 * values with the lowest priority are stored at the top of the heap and
 * will be the first returned.  If a heap is a max heap
 * (@ref BINARY_HEAP_TYPE_MAX), the values with the greatest priority are
 * stored at the top of the heap.
 */
typedef enum {
 /** A minimum heap. */
 BINARY_HEAP_TYPE_MIN,
 /** A maximum heap. */
 BINARY_HEAP_TYPE_MAX
} BinaryHeapType;
/**
 * A value stored in a @ref BinaryHeap.
 */
typedef void *BinaryHeapValue;
/**
 * A null @ref BinaryHeapValue.
 */
/**
 * Type of function used to compare values in a binary heap.
 *
 * @param value1           The first value.
 * @param value2           The second value.
 * @return                 A negative number if value1 is less than value2,
 *                         a positive number if value1 is greater than value2,
 *                         zero if the two are equal.
 */
typedef int (*BinaryHeapCompareFunc)(BinaryHeapValue value1,
                                     BinaryHeapValue value2);
/**
 * A binary heap data structure.
 */
typedef struct _BinaryHeap BinaryHeap;
/**
 * Create a new @ref BinaryHeap.
 *
 * @param heap_type        The type of heap: min heap or max heap.
 * @param compare_func     Pointer to a function used to compare the priority
 *                         of values in the heap.
 * @return                 A new binary heap, or NULL if it was not possible
 *                         to allocate the memory.
 */
BinaryHeap *binary_heap_new(BinaryHeapType heap_type,
                            BinaryHeapCompareFunc compare_func);
/**
 * Destroy a binary heap.
 *
 * @param heap             The heap to destroy.
 */
void binary_heap_free(BinaryHeap *heap);
/**
 * Insert a value into a binary heap.
 *
 * @param heap             The heap to insert into.
 * @param value            The value to insert.
 * @return                 Non-zero if the entry was added, or zero if it
 *                         was not possible to allocate memory for the new
 *                         entry.
 */
int binary_heap_insert(BinaryHeap *heap, BinaryHeapValue value);
/**
 * Remove the first value from a binary heap.
 *
 * @param heap             The heap.
 * @return                 The first value in the heap, or
 *                         @ref BINARY_HEAP_NULL if the heap is empty.
 */
BinaryHeapValue binary_heap_pop(BinaryHeap *heap);
/**
 * Find the number of values stored in a binary heap.
 *
 * @param heap             The heap.
 * @return                 The number of values in the heap.
 */
unsigned int binary_heap_num_entries(BinaryHeap *heap);
/* malloc() / free() testing */
struct _BinaryHeap {
 BinaryHeapType heap_type;
 BinaryHeapValue *values;
 unsigned int num_values;
 unsigned int alloced_size;
 BinaryHeapCompareFunc compare_func;
};
/* Null value that can be returned without creating a local variable */
static const BinaryHeapValue binary_heap_null_value = ((void *) 0);
static int binary_heap_cmp(BinaryHeap *heap, BinaryHeapValue data1,
                           BinaryHeapValue data2)
{
 if (heap->heap_type == BINARY_HEAP_TYPE_MIN) {
  __attribute__((calls(int_compare))) return heap->compare_func(data1, data2);
 } else {
  __attribute__((calls(int_compare))) return -heap->compare_func(data1, data2);
 }
}
BinaryHeap *binary_heap_new(BinaryHeapType heap_type,
                            BinaryHeapCompareFunc compare_func)
{
 BinaryHeap *heap;
 heap = malloc(sizeof(BinaryHeap));
 if (heap == ((void *)0)) {
  return ((void *)0);
 }
 heap->heap_type = heap_type;
 heap->num_values = 0;
 heap->compare_func = compare_func;
 /* Initial size of 16 elements */
 heap->alloced_size = 16;
 heap->values = malloc(sizeof(BinaryHeapValue) * heap->alloced_size);
 if (heap->values == ((void *)0)) {
  free(heap);
  return ((void *)0);
 }
 return heap;
}
void binary_heap_free(BinaryHeap *heap)
{
 free(heap->values);
 free(heap);
}
int binary_heap_insert(BinaryHeap *heap, BinaryHeapValue value)
{
 BinaryHeapValue *new_values;
 unsigned int index;
 unsigned int new_size;
 unsigned int parent;
 /* Possibly realloc the heap to a larger size */
 if (heap->num_values >= heap->alloced_size) {
  /* Double the table size */
  new_size = heap->alloced_size * 2;
  new_values =
      realloc(heap->values, sizeof(BinaryHeapValue) * new_size);
  if (new_values == ((void *)0)) {
   return 0;
  }
  heap->alloced_size = new_size;
  heap->values = new_values;
 }
 /* Add to the bottom of the heap and start from there */
 index = heap->num_values;
 ++heap->num_values;
 /* Percolate the value up to the top of the heap */
 while (index > 0) {
  /* The parent index is found by halving the node index */
  parent = (index - 1) / 2;
  /* Compare the node with its parent */
  if (binary_heap_cmp(heap, heap->values[parent], value) < 0) {
   /* Ordered correctly - insertion is complete */
   break;
  } else {
   /* Need to swap this node with its parent */
   heap->values[index] = heap->values[parent];
   /* Advance up to the parent */
   index = parent;
  }
 }
 /* Save the new value in the final location */
 heap->values[index] = value;
 return 1;
}
BinaryHeapValue binary_heap_pop(BinaryHeap *heap)
{
 BinaryHeapValue result;
 BinaryHeapValue new_value;
 unsigned int index;
 unsigned int next_index;
 unsigned int child1, child2;
 /* Empty heap? */
 if (heap->num_values == 0) {
  return binary_heap_null_value;
 }
 /* Take the value from the top of the heap */
 result = heap->values[0];
 /* Remove the last value from the heap; we will percolate this down
	 * from the top. */
 new_value = heap->values[heap->num_values - 1];
 --heap->num_values;
 /* Percolate the new top value down */
 index = 0;
 for (;;) {
  /* Calculate the array indexes of the children of this node */
  child1 = index * 2 + 1;
  child2 = index * 2 + 2;
  if (child1 < heap->num_values &&
      binary_heap_cmp(heap, new_value, heap->values[child1]) >
          0) {
   /* Left child is less than the node.  We need to swap
			 * with one of the children, whichever is less. */
   if (child2 < heap->num_values &&
       binary_heap_cmp(heap, heap->values[child1],
                       heap->values[child2]) > 0) {
    next_index = child2;
   } else {
    next_index = child1;
   }
  } else if (child2 < heap->num_values &&
             binary_heap_cmp(heap, new_value,
                             heap->values[child2]) > 0) {
   /* Right child is less than the node.  Swap with the
			 * right child. */
   next_index = child2;
  } else {
   /* Node is less than both its children. The heap
			 * condition is satisfied.  * We can stop percolating
			 * down. */
   heap->values[index] = new_value;
   break;
  }
  /* Swap the current node with the least of the child nodes. */
  heap->values[index] = heap->values[next_index];
  /* Advance to the child we chose */
  index = next_index;
 }
 return result;
}
unsigned int binary_heap_num_entries(BinaryHeap *heap)
{
 return heap->num_values;
}
BinaryHeap *calg_binary_new(void) {
    return binary_heap_new(BINARY_HEAP_TYPE_MIN, int_compare);
}
