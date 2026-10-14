#!/usr/bin/env Rscript
suppressPackageStartupMessages({library(jsonlite); library(ggplot2); library(patchwork)})
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 3, !dir.exists(args[3]))
cycles <- fromJSON(args[1], simplifyVector = FALSE)
report <- fromJSON(args[2], simplifyVector = FALSE)
stopifnot(report$status == "complete", length(cycles) == 288, length(report$participants) == 120)
features <- function(x) {
  stopifnot(length(x) == 101, all(is.finite(x)), all(x >= 0), sum(x) > 0)
  c(sum(x * (0:100)) / sum(x), which.max(x) - 1)
}
# Check every saved participant result, not just the illustrative case.
maximum_error <- 0
for (r in report$participants) {
  cs <- Filter(function(z) z$study == r$study && z$participant == r$participant && z$muscle == r$muscle, cycles)
  stopifnot(identical(vapply(cs, function(z) z$cycle, ""), unlist(r$cycle_ids)))
  x <- do.call(rbind, lapply(cs, function(z) unlist(z$curves[[r$condition]])))
  per_cycle <- t(apply(x, 1, features))
  a <- features(apply(x, 2, median)); b <- apply(per_cycle, 2, median)
  maximum_error <- max(maximum_error, abs(a-unlist(r$A)), abs(b-unlist(r$B)),
                       abs(per_cycle-do.call(rbind,lapply(r$cycle_features,unlist))))
}
stopifnot(maximum_error < 1e-12)

selected <- Filter(function(r) r$study == "study_38" && r$participant == "3" && r$muscle == "tibialis anterior", cycles)
stopifnot(length(selected) == 5)
x <- do.call(rbind, lapply(selected, function(r) unlist(r$curves$common_cycle)))
peaks <- apply(x, 1, function(z) which.max(z)-1)
representative <- apply(x,2,median)
peak_waveform <- which.max(representative)-1
peak_median <- median(peaks)
stopifnot(identical(as.numeric(peaks),c(76,6,69,80,6)), peak_waveform == 6, peak_median == 69)
long <- do.call(rbind,lapply(1:5,function(i) data.frame(cycle=factor(paste("Cycle",i), levels=paste("Cycle",1:5)),
  phase=0:100,value=x[i,])))
maxima <- data.frame(cycle=factor(paste("Cycle",1:5),levels=paste("Cycle",1:5)),
  phase=as.numeric(peaks),value=x[cbind(1:5,peaks+1)],label=paste0(peaks,"%"))
waveform_data <- data.frame(phase=0:100,value=representative)
peak_data <- data.frame(row=c(5:1,0),phase=c(peaks,peak_median),
  label=paste0(c(peaks,peak_median),"%"), kind=c(rep("Cycle",5),"Median"))
blue <- "#7BA4B0"; peach <- "#CAA38B"
theme_set(theme_classic(base_size=12,base_family="sans")+theme(
 text=element_text(colour="black"),axis.text=element_text(colour="black"),
 plot.title=element_text(size=14,face="bold",margin=margin(b=5)),
 plot.subtitle=element_text(size=10.5,margin=margin(b=10)),
 strip.background=element_blank(),strip.text=element_text(face="bold"),
 panel.spacing.x=grid::unit(1,"lines"),plot.margin=margin(10,13,10,10),legend.position="none"))
a <- ggplot(long,aes(phase,value)) + geom_line(colour=blue,linewidth=.85) +
 geom_point(data=maxima,size=2.5,shape=21,fill=peach,colour="black",stroke=.3) +
 geom_text(data=maxima,aes(label=label),nudge_y=.13,size=3.5) +
 facet_wrap(~cycle,nrow=1) +
 scale_x_continuous(limits=c(0,100),breaks=c(0,50,100),expand=expansion(mult=c(.01,.025))) +
 scale_y_continuous(limits=c(0,1.23),breaks=c(0,.5,1),expand=expansion(mult=c(0,.01))) +
 labs(title="A  The same five cycles",subtitle="Lencioni et al. (2019) · participant 3 · tibialis anterior",
      x="Gait cycle (%)",y="Scaled EMG envelope")
b <- ggplot(waveform_data,aes(phase,value)) +
 geom_line(colour=blue,linewidth=1) +
 geom_segment(aes(x=peak_waveform,xend=peak_waveform,y=0,yend=representative[peak_waveform+1]),
              data=data.frame(phase=0,value=0),linetype="dotted",colour=blue,linewidth=.6) +
 geom_point(data=data.frame(phase=peak_waveform,value=representative[peak_waveform+1]),size=3.5,
            shape=21,fill=blue,colour="black",stroke=.4) +
 annotate("text",x=21,y=1.10,label="Peak: 6%",hjust=0,size=4.3,fontface="bold") +
 scale_x_continuous(limits=c(0,100),breaks=c(0,20,40,60,80,100),expand=expansion(mult=c(.01,.025))) +
 scale_y_continuous(limits=c(0,1.23),breaks=c(0,.5,1),expand=expansion(mult=c(0,.01))) +
 labs(title="B  Summarize waveforms, then find the peak",
      subtitle="Pointwise median envelope → maximum location",x="Gait cycle (%)",y="Scaled EMG envelope")
c <- ggplot(peak_data,aes(phase,row)) +
 geom_hline(yintercept=.5,colour="grey78",linewidth=.4) +
 geom_segment(aes(x=0,xend=100,yend=row),colour="grey90",linewidth=.4) +
 geom_point(aes(fill=kind,shape=kind),size=3.3,colour="black",stroke=.35) +
 geom_text(aes(label=label),nudge_y=.24,size=3.6) +
 scale_fill_manual(values=c(Cycle=peach,Median=peach)) + scale_shape_manual(values=c(Cycle=21,Median=23)) +
 scale_x_continuous(limits=c(0,100),breaks=c(0,20,40,60,80,100),expand=expansion(mult=c(.01,.025))) +
 scale_y_continuous(limits=c(-.45,5.5),breaks=0:5,labels=c("Median",paste("Cycle",5:1)),expand=expansion(mult=0)) +
 labs(title="C  Find each peak, then summarize",
      subtitle="Five cycle-level peaks → median location",x="Peak location (% of gait cycle)",y=NULL)
figure <- (a / (b | c)) + plot_layout(heights=c(1,1.35)) +
 plot_annotation(caption="Same observations, different summaries — not a physiological delay.",
 theme=theme(plot.caption=element_text(size=12,colour="black",hjust=.5,margin=margin(t=10,b=5))))
dir.create(args[3],recursive=TRUE)
ggsave(file.path(args[3],"aggregation_order.png"),figure,width=12,height=9,dpi=240,bg="white")
ggsave(file.path(args[3],"aggregation_order.svg"),figure,width=12,height=9,bg="white")
write_json(list(source_files=args[1:2],condition="common_cycle",study="Lencioni et al. (2019)",
 participant="3",muscle="tibialis anterior",cycle_ids=lapply(selected,function(r)r$cycle),
 cycle_peaks=as.numeric(peaks),representative_peak=peak_waveform,median_cycle_peak=peak_median,
 waveform=waveform_data,cycles=long,checked_participant_muscle_conditions=120,
 maximum_feature_error=maximum_error,selection="Previously reported example chosen to explain disagreement; not a typical-participant claim",
 tie_rule="First maximum on the 101-point grid",median_rule="Linear 0–100% scale; not a circular summary"),
 file.path(args[3],"plot_data.json"),pretty=TRUE,auto_unbox=TRUE,digits=NA)
capture.output(sessionInfo(),file=file.path(args[3],"R_session.txt"))
message("Saved aggregation-order figure; all 120 participant-muscle-condition summaries verified.")
