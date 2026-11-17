#ifndef CALG_VERIFY_STDLIB_H
#define CALG_VERIFY_STDLIB_H
typedef unsigned long size_t;
#define NULL ((void *)0)
void *malloc(size_t size);
void *realloc(void *ptr, size_t size);
void free(void *ptr);
#endif
