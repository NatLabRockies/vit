// The C++ translation of init_par / grow_par: the algorithm body an agent
// writes. Each ALLOCATE in the Fortran becomes a vit_adopt_alloc[_lb] with the
// same lower bounds, published through the view; the generated wrapper adopts it.
#include <cmath>
#include "vit_types.h"

static double* alloc_d(size_t n) { return static_cast<double*>(vit_adopt_alloc(n * sizeof(double))); }

void init_par(int n, int fail, part_view_t* p) {
    if (fail == 2) return;
    p->NumTabs = n;
    p->secondVals = alloc_d(n);
    p->n_secondVals = n;
    for (int i = 1; i <= n; ++i) p->secondVals[i - 1] = 0.1 * i + 1.0 / 3.0;
    if (fail == 1) return;
    tabt_view_t* t = static_cast<tabt_view_t*>(vit_adopt_alloc(n * sizeof(tabt_view_t)));
    p->Table = t;
    p->n_Table = n;
    for (int i = 1; i <= n; ++i) {
        tabt_view_t& T = t[i - 1];
        T.NumAlf = 3 + i;
        T.Re = 1.0e6 * i;
        int na = T.NumAlf;
        T.Alpha = alloc_d(na); T.n_Alpha = na;
        T.Coefs = alloc_d(na * 2); T.n_Coefs_rows = na; T.n_Coefs_cols = 2;
        // ALLOCATE(p%Table(i)%Spl(NumAlf - 1, 2, 0:3)): third lower bound 0
        T.Spl = static_cast<double*>(vit_adopt_alloc_lb((na - 1) * 2 * 4 * sizeof(double), 3, 1, 1, 0));
        T.n_Spl_dim1 = na - 1; T.n_Spl_dim2 = 2; T.n_Spl_dim3 = 4;
        for (int j = 1; j <= na; ++j) {
            T.Alpha[j - 1] = std::sin(0.3 * j + i);
            T.Coefs[(j - 1) + 0 * na] = std::cos(0.7 * j) / i;
            T.Coefs[(j - 1) + 1 * na] = std::exp(-0.1 * j * i);
        }
        int d1 = na - 1;
        for (int k = 0; k <= 3; ++k)
            for (int j = 1; j <= d1; ++j) {
                // Spl(j, c, k) column-major with third index k from 0
                T.Spl[(j - 1) + 0 * d1 + k * d1 * 2] = T.Alpha[j - 1] * (k + 1) / 7.0;
                T.Spl[(j - 1) + 1 * d1 + k * d1 * 2] = T.Coefs[(j - 1) + 1 * na] - k;
            }
    }
}

void grow_par(part_view_t* p) {
    int n = p->n_secondVals;
    double* g = alloc_d(n + 1);
    for (int i = 0; i < n; ++i) g[i] = p->secondVals[i];
    g[n] = -2.5;
    p->secondVals = g;          // REPLACED: the wrapper adopts it
    p->n_secondVals = n + 1;
    p->Table[0].Alpha[0] = 42.0; // IN PLACE: Fortran storage, left alone
    p->NumTabs = p->NumTabs + 100;
}
