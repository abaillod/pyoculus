## @file qfm.py: class for generating the (weighted) Quadratic Flux Minimising (QFM) surfaces
#  @brief class for generating the QFMs
#  @author Zhisong Qu (zhisong.qu@anu.edu.au)
#

from .base_solver import BaseSolver
from pyoculus.problems import ToroidalBfield
import numpy as np

nax = np.newaxis


class QFM(BaseSolver):
    def __init__(
        self,
        problem: ToroidalBfield,
        params=dict(),
        integrator=None,
        integrator_params=dict(),
    ):
        """! Set up the class of the fixed point finder
        @param problem must inherit pyoculus.problems.ToroidalBfield, the problem to solve
        @param params dict, the parameters for the solver
        @param integrator the integrator to use, must inherit \pyoculus.integrators.BaseIntegrator, if set to None by default using RKIntegrator (not used here)
        @param integrator_params dict, the parmaters passed to the integrator (not used here)

        <code> params['pqMpol']=8 </code> -- Fourier resolution multiplier for poloidal direction
        <code> params['pqNtor']=4 </code> -- Fourier resolution multiplier for toroidal direction
        <code> params['nfft_multiplier']=4 </code> -- the extended (multiplier) resolution for FFT
        <code> params['action_gradient_mode']='real' </code> -- 'real' or 'fourier', compute the action gradient in real space or fourier space
        """

        if "ntheta" not in params.keys():
            params["ntheta"] = 100

        if "nfft_multiplier" not in params.keys():
            params["nfft_multiplier"] = 2

        if "pqNtor" not in params.keys():
            params["pqNtor"] = 4

        if "pqMpol" not in params.keys():
            params["pqMpol"] = 8

        if "action_gradient_mode" not in params.keys():
            params["action_gradient_mode"] = "real"

        self._MM = params["nfft_multiplier"] * 2
        self._pqNtor = params["pqNtor"]
        self._pqMpol = params["pqMpol"]
        self._action_gradient_mode = params["action_gradient_mode"]
        self.Nfp = problem.Nfp

        integrator_params["ode"] = problem.f

        super().__init__(problem, params, integrator, integrator_params)

    def straighten_boundary(self, rho=1, tol=1e-9, niter=10):
        """! Convert a boundary surface to have straight field line
        For a boundary surface with rho=constant, find the function \f$\lambda(\vartheta, \zeta)\f$ with transformation
        \f[
            \theta = \vartheta + \lambda(\vartheta, \zeta),
        \f]
        such that \f$B^{\vartheta} / B^{\zeta} = 1 / q\f$ is a constant on the surface.

        The transformation gives
        \f[
            B^{\vartheta} = \frac{B^\theta - \lambda_\zeta B^\zeta}{1 + \lambda_\vartheta},
        \f]
        and no change to \f$B^\zeta\f$.
        Therefore, we get
        \f[
            \frac{B^\theta}{B^\zeta}(\theta = \vartheta + \lambda(\vartheta, \zeta), \zeta) = \frac{1}{q} (1 + \lambda_\vartheta) + \lambda_\zeta
        \f]
        @param rho the boundary surface coordinate
        @param tol the tolerance to stop iteration
        @param niter the number of iterations
        @returns tsn, tcn  the sine and cosine coefficient of the map \f$\lambda\f$ in \f$\theta = \vartheta + \lambda(\vartheta, \zeta)\f$
        """
        mpol = self._pqMpol
        ntor = mpol = self._pqNtor

        nfft_theta = self._MM * mpol
        nfft_zeta = self._MM * ntor

        rarr = np.ones([1]) * rho
        zarr = np.linspace(0, np.pi * 2, nfft_zeta, endpoint=False)

        rarr = np.broadcast_to(rarr[:, np.newaxis, np.newaxis], [1, nfft_theta, nfft_zeta])
        zarr = np.broadcast_to(zarr[np.newaxis, np.newaxis, :], [1, nfft_theta, nfft_zeta])

        lambda_cn = np.zeros([mpol + 1, 2 * ntor + 1])
        lambda_sn = np.zeros([mpol + 1, 2 * ntor + 1])
        iota = 0

        mlist = np.arange(0, mpol + 1)
        nlist = np.concatenate([np.arange(0, ntor + 1), np.arange(-ntor, 0)]) * self.Nfp

        for i in range(niter):
            iota_old = iota
            lambda_cn_old = lambda_cn.copy()
            lambda_sn_old = lambda_sn.copy()

            lambda_real = irfft2D(lambda_cn, lambda_sn, nfft_theta, nfft_zeta)
            tarr = (
                np.linspace(0, np.pi * 2, nfft_theta, endpoint=False)[:, nax]
                + lambda_real
            )

            coords = np.stack([rarr.flatten(), tarr.flatten(), zarr.flatten()], -1)

            B = self._problem.B_many(coords)

            Bs = np.reshape(B[:, 0], [nfft_theta, nfft_zeta])
            Bt = np.reshape(B[:, 1], [nfft_theta, nfft_zeta])
            Bz = np.reshape(B[:, 2], [nfft_theta, nfft_zeta])

            Bt_over_Bz = Bt / Bz

            cn, sn = rfft2D(Bt_over_Bz, mpol, ntor)

            iota = cn[0, 0]

            lambda_cn[0, 1:] = sn[0, 1:] / (-mlist[0,nax] * iota + nlist[nax,1:])
            lambda_sn[0, 1:] = cn[0, 1:] / (+mlist[0,nax] * iota - nlist[nax,1:])
            lambda_cn[1:, :] = sn[1:, :] / (-mlist[1:,nax] * iota + nlist[nax,:])
            lambda_sn[1:, :] = cn[1:, :] / (+mlist[1:,nax] * iota - nlist[nax,:])
            lambda_cn[0,0] = 0
            lambda_sn[0,0] = 0

            erriota = np.abs(iota - iota_old)
            errcn = np.max(np.abs(lambda_cn - lambda_cn_old))
            errsn = np.max(np.abs(lambda_sn - lambda_sn_old))

            if np.max([erriota, errcn, errsn]) < tol:
                break

        return iota, lambda_sn, lambda_cn


    def action(self, pp, qq, rguess=0.5, method="hybr"):
        from scipy.optimize import root

        # shorthand
        MM = self._MM
        pqNtor = self._pqNtor
        pqMpol = self._pqMpol
        iota = pp / qq

        ## The number of toroidal modes
        qN = qq * pqNtor
        ## The number of action curves with different area poloidally to be found, note that this has nothing to do with pqMpol
        fM = MM * pqNtor
        ## The number of points poloidally after folding the curves
        qfM = qq * MM * pqNtor
        ## The number of toroidal points for action gradient calculation
        Nfft = MM * qq * pqNtor
        ## The zeta distance between toroidal points
        dz = 2 * np.pi / (MM * pqNtor)
        self.dz = dz
        ## The theta distance between poloidal action curves
        dt = (np.pi * 2 / qq) / fM

        self._nlist = np.arange(0, qN + 1)
        self._zeta = np.arange(0, Nfft) * dz
        self._nzq = self._nlist[:, nax] * self._zeta[nax, :] / qq
        self._cnzq = np.cos(self._nzq)
        self._snzq = np.sin(self._nzq)

        rcnarr = np.zeros([fM, qN + 1])
        tcnarr = np.zeros([fM, qN + 1])
        rsnarr = np.zeros([fM, qN + 1])
        tsnarr = np.zeros([fM, qN + 1])
        nvarr = np.zeros(fM)

        for jpq in range(fM):

            a = jpq * dt

            if jpq == 0:
                nv0 = 0
                rcn0 = np.zeros(qN + 1)
                tcn0 = np.zeros(qN + 1)
                rsn0 = np.zeros(qN + 1)
                tsn0 = np.zeros(qN + 1)
                rcn0[0] = rguess
                tcn0[0] = 0
            else:
                nv0 = nvarr[jpq - 1].copy()
                rcn0 = rcnarr[jpq - 1, :].copy()
                tcn0 = tcnarr[jpq - 1, :].copy()
                rsn0 = rsnarr[jpq - 1, :].copy()
                tsn0 = tsnarr[jpq - 1, :].copy()
                tcn0[0] += dt

            xx0 = self._pack_dof(nv0, rcn0, tsn0, rsn0, tcn0)
            sol = root(
                self.action_gradient,
                xx0,
                args=(pp, qq, a, self._action_gradient_mode),
                method=method,
                tol=1e-8,
            )
            success = sol.success
            if success:
                nv, rcn, tsn, rsn, tcn = self._unpack_dof(sol.x)
            else:
                raise RuntimeError(
                    "QFM orbit for pp="
                    + str(pp)
                    + ",qq="
                    + str(qq)
                    + ",a="
                    + str(a)
                    + " not found."
                )

            rcnarr[jpq, :] = rcn
            tsnarr[jpq, :] = tsn
            tcnarr[jpq, :] = tcn
            rsnarr[jpq, :] = rsn
            nvarr[jpq] = nv

        # wrap the lines into a 2D surface in (alpha, zeta)
        r = irfft1D(rcnarr, rsnarr, MM // 2)
        z = np.linspace(0, 2 * qq * np.pi, r.shape[-1], endpoint=False)
        # Note that we will remove the DC part ~ alpha and the p/q*zeta part which will be counted seperately
        tcnarr[:, 0] = 0
        t = irfft1D(tcnarr, tsnarr, MM // 2)

        r2D_alpha = np.zeros([qfM, Nfft])
        t2D_alpha = np.zeros([qfM, Nfft])

        for i in range(qq):
            idx = np.mod(pp * i, qq)
            r2D_alpha[idx * fM : (idx + 1) * fM, 0 : (qq - i) * pqNtor * MM] = r[
                :, i * pqNtor * MM :
            ]
            r2D_alpha[idx * fM : (idx + 1) * fM, (qq - i) * pqNtor * MM :] = r[
                :, 0 : i * pqNtor * MM
            ]
            t2D_alpha[idx * fM : (idx + 1) * fM, 0 : (qq - i) * pqNtor * MM] = t[
                :, i * pqNtor * MM :
            ]
            t2D_alpha[idx * fM : (idx + 1) * fM, (qq - i) * pqNtor * MM :] = t[
                :, 0 : i * pqNtor * MM
            ]

        r2D_vartheta = np.zeros([qfM, MM * pqNtor])
        t2D_vartheta = np.zeros([qfM, MM * pqNtor])

        # convert (alpha, zeta) into (vartheta, zeta), knowing alpha + p/q * zeta = vartheta
        # Therefore, for the same theta, as we move in zeta, alpha should decrease p/q * zeta
        # the alpha angle inteval is 2pi / fM / q, the zeta inteval is 2pi / fM
        # therefore if we move one grid in zeta, we should move -p grids in alpha
        for i in range(MM * pqNtor):
            idx = np.mod(np.arange(0, qfM) - i * pp, qfM)
            r2D_vartheta[:, i] = r2D_alpha[idx, i]
            t2D_vartheta[:, i] = t2D_alpha[idx, i]

        scn_surf, ssn_surf = rfft2D(r2D_vartheta, pqMpol, pqNtor)
        tcn_surf, tsn_surf = rfft2D(t2D_vartheta, pqMpol, pqNtor)

        return scn_surf, tsn_surf, ssn_surf, tcn_surf

    def action_gradient(self, xx, pp, qq, a, mode="real"):
        """! Computes the action gradient, being used in root finding
        @param xx  the packed degrees of freedom. It should contain rcn, tsn, rsn, tcn, nv.
        @param pp  the poloidal periodicity of the island, should be an integer
        @param qq  the toroidal periodicity of the island, should be an integer
        @param a   the target area
        @param mode  "real" or "fourier", to compute the equations in real space or fourier space
        @returns ff  the equtions to find zeros, see below.

        Construct the Fourier transform of \f$B^\vartheta_i / B^\zeta_i\f$ and \f$B^\rho_i / B^\zeta_i + \bar \nu / (J B^\zeta_i)\f$,
        \f[
        B^\t / B^\z & = & f^c_0 + \sum_{n=1}^{qN} \left[ f^c_n \cos(n\z/q) + f^s_n \sin(n\z/q) \right], \label{eqn:f}
        \f] \f[
        B^\rho / B^\z + \bar \nu / \sqrt g B^\z & = & g^c_0 + \sum_{n=1}^{qN} \left[ g^c_n \cos(n\z/q) + g^s_n \sin(n\z/q) \right], \label{eqn:g} 
        \f]
        
        \item The Fourier harmonics of $\dot\rho$ and $\dot\t$ are given directly by, 
        \begin{eqnarray}
        \dot \t(\z) & = & p/q + \sum_{n=1}^{qN} \left[ - \t^c_n \sin(n\z/q) + \t^s_n\cos(n\z/q) \right](n/q), \label{eqn:tdot} \\
        \dot \rho(\z) & = & \sum_{n=1}^{qN} \left[ - \rho^c_n \sin(n\z/q) + \rho^s_n\cos(n\z/q) \right] (n/q), \label{eqn:rdot}
        \label{eqn:dtrialcurve}
        \end{eqnarray}
        """
        # shorthand
        iota = pp / qq
        qN = (xx.size - 1) // 4

        # unpack dof
        nv, rcn, tsn, rsn, tcn = self._unpack_dof(xx)

        if mode == "fourier":
            r = np.sum(rcn[:, nax] * self._cnzq, axis=0) + np.sum(
                rsn[:, nax] * self._snzq, axis=0
            )
            t = np.sum(tcn[:, nax] * self._cnzq, axis=0) + np.sum(
                tsn[:, nax] * self._snzq, axis=0
            )
            z = self._zeta
            t += iota * z
        elif mode == "real":
            r = irfft1D(rcn, rsn)
            z = np.linspace(0, 2 * qq * np.pi, r.size, endpoint=False)
            t = irfft1D(tcn, tsn) + iota * z
            rdot = irfft1D(rsn * self._nlist / qq, -rcn * self._nlist / qq)
            tdot = irfft1D(tsn * self._nlist / qq, -tcn * self._nlist / qq) + iota
        else:
            raise ValueError("space should be 'real' or 'fourier' ")

        # area = ( np.sum( t ) + np.pi * pp) * self.dz / (qq*2*np.pi) - pp * np.pi
        area = tcn[0]

        B = self._problem.B_many(np.stack([r, t, z], -1))

        gBr = B[:, 0]
        gBt = B[:, 1]
        gBz = B[:, 2]

        rhs_tdot = gBt / gBz
        rhs_rdot = gBr / gBz - nv / gBz

        # now pack the function values
        ff = np.zeros_like(xx)
        ff[0] = area - a

        if mode == "fourier":
            rhs_tdot_fft_cos, rhs_tdot_fft_sin = rfft1D(rhs_tdot)
            rhs_rdot_fft_cos, rhs_rdot_fft_sin = rfft1D(rhs_rdot)

            ff[1 : qN + 2] = rsn * self._nlist / qq - rhs_rdot_fft_cos[0 : qN + 1]
            ff[qN + 2 : 2 * qN + 1] = (
                -rcn * self._nlist / qq - rhs_rdot_fft_sin[0 : qN + 1]
            )[1:-1]
            ff[2 * qN + 1 : 3 * qN + 2] = (
                tsn * self._nlist / qq - rhs_tdot_fft_cos[0 : qN + 1]
            )
            ff[2 * qN + 1] += iota
            ff[3 * qN + 2 :] = (-tcn * self._nlist / qq - rhs_tdot_fft_sin[0 : qN + 1])[
                1:-1
            ]
        elif mode == "real":
            ff[1 : 2 * qN + 1] = rdot - rhs_rdot
            ff[2 * qN + 1 :] = tdot - rhs_tdot

        return ff

    def _unpack_dof(self, xx):
        """! Unpack the degrees of freedom into Fourier harmonics
        @param xx  the packed degrees of freedom
        @returns nv, rcn, tsn, rsn, tcn
        """
        qN = (xx.size - 1) // 4
        nv = xx[0] - 1
        rcn = xx[1 : qN + 2] - 1
        tsn = np.concatenate([[0], xx[qN + 2 : 2 * qN + 1] - 1, [0]])
        rsn = np.concatenate([[0], xx[2 * qN + 1 : 3 * qN] - 1, [0]])
        tcn = xx[3 * qN :] - 1

        return nv, rcn, tsn, rsn, tcn

    def _pack_dof(self, nv, rcn, tsn, rsn, tcn):
        """! Unpack the degrees of freedom into Fourier harmonics
        @param nv
        @param rcn
        @param tsn
        @param rsn
        @param tcn
        """
        xx = np.concatenate([[nv], rcn, tsn[1:-1], rsn[1:-1], tcn]) + 1

        return xx


def rfft1D(f):
    """! perform 1D Fourier transform from real space to cosine and sine
    @param f the data in real space. If f is 2D, then the last axis will be the axis along which FFT is computed
    @returns cosout, sinout the cosine and sine components
    """
    Nfft = f.shape[-1]
    ffft = np.fft.rfft(f)
    cosout = np.real(ffft) / Nfft * 2
    cosout[..., 0] /= 2

    sinout = -np.imag(ffft) / Nfft * 2
    sinout[..., 0] = 0

    return cosout, sinout


def irfft1D(cos_in, sin_in, nfft_multiplier=1):
    """! perform 1D inverse Fourier transform from cosine and sine to real space
    @param cos_in The cosine components. If cos_in is 2D, then the last axis will be the axis along which FFT was computed
    @param sin_in The sine components
    @param nfft_multiplier The number of output points will be this*(cos_in.shape[-1] - 1)
    @returns the function value in real space
    """
    Nfft = nfft_multiplier * (cos_in.shape[-1] - 1)
    sin_in_new = sin_in.copy()
    sin_in_new[0] = 0
    sin_in_new[-1] = 0
    ffft = (cos_in - complex(0, 1) * sin_in) * Nfft
    ffft[..., 0] *= 2
    result = np.fft.irfft(ffft, 2 * Nfft)
    return result


def rfft2D(f, mpol=None, ntor=None):
    """! perform 2D Fourier transform from real space to cosine and sine
    @param f the data in real space. If f is 2D, then the last axis will be the axis along which FFT is computed
    @returns fftcos, fftsin the cosine and sine components, m from 0 to mpol, n from -ntor to ntor
    """
    Nfft1 = f.shape[-2]
    Nfft2 = f.shape[-1]

    fftout = np.fft.rfft2(f, axes=[-1, -2])
    fftcos = np.real(fftout) / Nfft1 / Nfft2 * 2
    fftcos[0, :] /= 2
    fftcos[-1, :] /= 2
    fftsin = -np.imag(fftout) / Nfft1 / Nfft2 * 2
    fftsin[0, :] /= 2
    fftsin[-1, :] /= 2

    if mpol is None:
        mpol = Nfft1 // 4
    if ntor is None:
        ntor = Nfft2 // 4

    cn = np.zeros([mpol + 1, 2 * ntor + 1])
    sn = np.zeros([mpol + 1, 2 * ntor + 1])

    # 1. assuming mpol and ntor are lower than that of the data
    # so we just need to truncate it, otherwise we will need to pad it
    idxlist = np.concatenate([[0], -np.arange(1, ntor + 1), np.arange(ntor, 0, -1)])
    cn[0 : mpol + 1, :] = fftcos[0 : mpol + 1, idxlist]
    sn[0 : mpol + 1, :] = fftsin[0 : mpol + 1, idxlist]

    # 2, mpol is higher than data, ntor is lower

    return cn, sn


def irfft2D(cn, sn, nfft_theta=None, nfft_zeta=None):
    """! perform 2D Fourier transform from real space to cosine and sine
    @param cn the cosine components
    @param sn the sine components
    @param nfft_theta, the number of theta points on output
    @param nfft_zeta, the number of zeta points on output
    @returns fout the function output
    """
    mpol = cn.shape[0] - 1
    ntor = (cn.shape[1] - 1) // 2

    if nfft_theta is None:
        nfft_theta = mpol * 4
    if nfft_zeta is None:
        nfft_zeta = ntor * 4

    mpol_new = nfft_theta // 2
    ntor_new = nfft_zeta // 2

    # now we pad cn and sn with zeros
    cn_pad = np.zeros([mpol_new + 1, 2 * ntor_new])
    sn_pad = np.zeros([mpol_new + 1, 2 * ntor_new])

    idxlist = np.concatenate([[0], -np.arange(1, ntor + 1), np.arange(ntor, 0, -1)])

    cn_pad[0 : mpol + 1, idxlist] = cn
    sn_pad[0 : mpol + 1, idxlist] = sn

    cn_pad[0, :] *= 2
    cn_pad[-1, :] *= 2
    sn_pad[0, :] *= 2
    sn_pad[-1, :] *= 2

    fout = (
        np.fft.irfft2(cn_pad - complex(0, 1) * sn_pad, axes=[-1, -2])
        * nfft_theta
        * nfft_zeta
        / 2
    )

    return fout