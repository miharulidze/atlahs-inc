#include <stdio.h>
#include <stdlib.h>
#include "cuda_runtime.h"
#include "nccl.h"
#include "mpi.h"
#include <unistd.h>
#include <stdint.h>
#include <string.h>
#include <vector>
#include <algorithm>

#define MPICHECK(cmd) do {                          \
  int e = cmd;                                      \
  if( e != MPI_SUCCESS ) {                          \
    printf("Failed: MPI error %s:%d '%d'\n",        \
        __FILE__,__LINE__, e);   \
    exit(EXIT_FAILURE);                             \
  }                                                 \
} while(0)

#define CUDACHECK(cmd) do {                         \
  cudaError_t e = cmd;                              \
  if( e != cudaSuccess ) {                          \
    printf("Failed: Cuda error %s:%d '%s'\n",             \
        __FILE__,__LINE__,cudaGetErrorString(e));   \
    exit(EXIT_FAILURE);                             \
  }                                                 \
} while(0)

#define NCCLCHECK(cmd) do {                         \
  ncclResult_t r = cmd;                             \
  if (r!= ncclSuccess) {                            \
    printf("Failed, NCCL error %s:%d '%s'\n",             \
        __FILE__,__LINE__,ncclGetErrorString(r));   \
    exit(EXIT_FAILURE);                             \
  }                                                 \
} while(0)

static uint64_t getHostHash(const char* string) {
  uint64_t result = 5381;
  for (int c = 0; string[c] != '\0'; c++){
    result = ((result << 5) + result) ^ string[c];
  }
  return result;
}

static void getHostName(char* hostname, int maxlen) {
  gethostname(hostname, maxlen);
  for (int i=0; i< maxlen; i++) {
    if (hostname[i] == '.') {
        hostname[i] = '\0';
        return;
    }
  }
}

int main(int argc, char* argv[])
{
  std::vector<int> sizes;
  if (argc == 2 && argv[1][0] != '-') {
    sizes.push_back(atoi(argv[1]));
  } else if (argc == 4 && strcmp(argv[1], "--sweep") == 0) {
    int startSize = atoi(argv[2]);
    int maxSize = atoi(argv[3]);
    if (startSize <= 0 || maxSize <= 0 || startSize > maxSize) {
      printf("Invalid sweep sizes: start=%d max=%d\n", startSize, maxSize);
      return -1;
    }
    for (int s = startSize; s <= maxSize; s *= 2) sizes.push_back(s);
  } else if (argc == 3 && strcmp(argv[1], "--sizes") == 0) {
    // Comma-separated list, e.g. "1,2,4,8"
    char* list = strdup(argv[2]);
    char* saveptr = NULL;
    for (char* tok = strtok_r(list, ",", &saveptr); tok; tok = strtok_r(NULL, ",", &saveptr)) {
      int s = atoi(tok);
      if (s > 0) sizes.push_back(s);
    }
    free(list);
  } else {
    printf("Usage: %s <size>\n", argv[0]);
    printf("       %s --sweep <start_size> <max_size>\n", argv[0]);
    printf("       %s --sizes <comma_separated_sizes>\n", argv[0]);
    return -1;
  }

  sizes.erase(std::remove_if(sizes.begin(), sizes.end(), [](int s){ return s <= 0; }), sizes.end());
  if (sizes.empty()) {
    printf("No valid sizes provided.\n");
    return -1;
  }

  int maxSize = *std::max_element(sizes.begin(), sizes.end());

  int myRank, nRanks, localRank = 0;

  MPICHECK(MPI_Init(&argc, &argv));
  MPICHECK(MPI_Comm_rank(MPI_COMM_WORLD, &myRank));
  MPICHECK(MPI_Comm_size(MPI_COMM_WORLD, &nRanks));

  uint64_t hostHashs[nRanks];
  char hostname[1024];
  getHostName(hostname, 1024);
  hostHashs[myRank] = getHostHash(hostname);
  MPICHECK(MPI_Allgather(MPI_IN_PLACE, 0, MPI_DATATYPE_NULL, hostHashs, sizeof(uint64_t), MPI_BYTE, MPI_COMM_WORLD));
  for (int p=0; p<nRanks; p++) {
     if (p == myRank) break;
     if (hostHashs[p] == hostHashs[myRank]) localRank++;
  }

  int nDevices = 0;
  CUDACHECK(cudaGetDeviceCount(&nDevices));
  int device = (nDevices > 0) ? (localRank % nDevices) : 0;

  const char* cudaVisibleDevices = getenv("CUDA_VISIBLE_DEVICES");
  printf("The local rank is: %d (CUDA_VISIBLE_DEVICES=%s, visible_devices=%d, using_device=%d)\n",
         localRank, cudaVisibleDevices ? cudaVisibleDevices : "<unset>", nDevices, device);

  ncclUniqueId id;
  ncclComm_t comm;
  float *sendbuff, *recvbuff;
  cudaStream_t s;

  if (myRank == 0) ncclGetUniqueId(&id);
  MPICHECK(MPI_Bcast((void *)&id, sizeof(id), MPI_BYTE, 0, MPI_COMM_WORLD));

  CUDACHECK(cudaSetDevice(device));
  CUDACHECK(cudaMalloc(&sendbuff, maxSize * sizeof(float)));
  CUDACHECK(cudaMalloc(&recvbuff, maxSize * sizeof(float)));
  
  CUDACHECK(cudaMemset(sendbuff, 0, maxSize * sizeof(float)));
  CUDACHECK(cudaMemset(recvbuff, 0, maxSize * sizeof(float)));
 
  CUDACHECK(cudaStreamCreate(&s));

  NCCLCHECK(ncclCommInitRank(&comm, nRanks, id, myRank));

  for (size_t i = 0; i < sizes.size(); i++) {
    int size = sizes[i];
    if (myRank == 0) printf("Running allreduce size=%d\n", size);
    NCCLCHECK(ncclAllReduce((const void*)sendbuff, (void*)recvbuff, size, ncclFloat, ncclSum, comm, s));
    CUDACHECK(cudaStreamSynchronize(s));
  }

  CUDACHECK(cudaFree(sendbuff));
  CUDACHECK(cudaFree(recvbuff));

  ncclCommDestroy(comm);

  MPICHECK(MPI_Finalize());

  printf("[MPI Rank %d] Success \n", myRank);
  
  return 0;
}
