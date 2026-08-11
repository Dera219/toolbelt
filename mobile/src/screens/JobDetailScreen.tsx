import { useFocusEffect } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import React, { useCallback, useState } from "react";
import { Image, ScrollView, View } from "react-native";
import { ApiError, api, money } from "../api/client";
import type { Dispute, DisputeReason, Job, JobRatings, Offer, Payment } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { API_URL } from "../config";
import type { RootStackParamList } from "../navigation";
import { palette, radius, space, tradeMeta } from "../theme";
import {
  Badge,
  Body,
  Button,
  Caption,
  Card,
  ErrorText,
  FadeIn,
  Input,
  Pill,
  Price,
  Row,
  Screen,
  SectionHeader,
  Skeleton,
  StatusRail,
  Subtext,
  Title,
  successFeedback,
} from "../ui";

type Props = NativeStackScreenProps<RootStackParamList, "JobDetail">;

export default function JobDetailScreen({ route, navigation }: Props) {
  const { jobId } = route.params;
  const { user, mode } = useAuth();
  const [job, setJob] = useState<Job | null>(null);
  const [offers, setOffers] = useState<Offer[]>([]);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [ratings, setRatings] = useState<JobRatings | null>(null);
  const [dispute, setDispute] = useState<Dispute | null>(null);
  const [disputeOpen, setDisputeOpen] = useState(false);
  const [disputeReason, setDisputeReason] = useState<DisputeReason>("quality");
  const [disputeDetail, setDisputeDetail] = useState("");
  const [offerPrice, setOfferPrice] = useState("");
  const [offerMessage, setOfferMessage] = useState("");
  const [stars, setStars] = useState(5);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isCustomer = user != null && job != null && job.customer_id === user.id;
  const isAssignedWorker = user != null && job != null && job.assigned_worker_id === user.id;
  const myOffer = offers.find((o) => o.worker_id === user?.id) ?? null;

  const load = useCallback(async () => {
    try {
      const j = await api.job(jobId);
      setJob(j);
      const results = await Promise.allSettled([
        api.jobOffers(jobId),
        api.jobPayment(jobId),
        api.jobRatings(jobId),
        api.jobDispute(jobId),
      ]);
      setOffers(results[0].status === "fulfilled" ? results[0].value : []);
      setPayment(results[1].status === "fulfilled" ? results[1].value : null);
      setRatings(results[2].status === "fulfilled" ? results[2].value : null);
      setDispute(results[3].status === "fulfilled" ? results[3].value : null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load job");
    }
  }, [jobId]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const run = (fn: () => Promise<unknown>) => async () => {
    setError(null);
    setBusy(true);
    try {
      await fn();
      successFeedback();
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  if (job == null)
    return (
      <Screen>
        <Skeleton height={150} />
        <Skeleton height={100} />
        <ErrorText message={error} />
      </Screen>
    );

  const trade = tradeMeta(job.trade);
  const pending = offers.filter((o) => o.status === "pending");
  const canRate =
    job.status === "completed" && (isCustomer || isAssignedWorker) && ratings?.mine == null;

  return (
    <Screen>
      <FadeIn>
        <Card raised>
          <Row between>
            <Row gap={space.sm}>
              <View style={styles.tradeIcon}>
                <Title>{trade.icon}</Title>
              </View>
              <View>
                <Title>{job.title}</Title>
                <Caption>{trade.label}</Caption>
              </View>
            </Row>
            <Badge label={job.status} />
          </Row>

          <StatusRail status={job.status} />

          {job.photos.length > 0 && (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginTop: 4 }}>
              {job.photos.map((photo) => (
                <Image
                  key={photo.id}
                  source={{ uri: `${API_URL}${photo.url}` }}
                  style={{
                    width: 132,
                    height: 132,
                    borderRadius: radius.md,
                    marginRight: space.sm,
                  }}
                />
              ))}
            </ScrollView>
          )}

          {job.description !== "" && <Body>{job.description}</Body>}

          <Row gap={space.sm}>
            <Pill icon="📍" label={job.address_text} />
            {job.customer_provides_supplies && <Pill icon="🧴" label="Supplies provided" />}
          </Row>

          {job.budget_cents != null && (
            <Row between>
              <Caption>CUSTOMER BUDGET</Caption>
              <Price value={money(job.budget_cents, job.currency)} size="lg" />
            </Row>
          )}
        </Card>
      </FadeIn>

      {payment != null && (
        <FadeIn delay={60}>
          <Card>
            <Row between>
              <Title>Payment</Title>
              <Badge label={payment.status} />
            </Row>
            <Row between>
              <Subtext>{isAssignedWorker ? "You earn" : "Total"}</Subtext>
              <Price
                value={money(
                  isAssignedWorker ? payment.worker_net_cents : payment.amount_cents,
                  payment.currency
                )}
              />
            </Row>
            {isAssignedWorker && (
              <Caption>
                After the {money(payment.platform_fee_cents, payment.currency)} platform fee
              </Caption>
            )}
            {payment.refunded_cents > 0 && (
              <Caption>Refunded {money(payment.refunded_cents, payment.currency)}</Caption>
            )}
          </Card>
        </FadeIn>
      )}

      {/* Customer: offers to review */}
      {isCustomer && job.status === "open" && (
        <>
          <SectionHeader title={pending.length ? `${pending.length} offer${pending.length > 1 ? "s" : ""}` : "Offers"} />
          {offers.length === 0 && (
            <Card>
              <Subtext>No offers yet — nearby pros have been notified.</Subtext>
            </Card>
          )}
          {offers.map((offer, i) => (
            <FadeIn key={offer.id} delay={i * 60}>
              <Card>
                <Row between>
                  <Price value={money(offer.price_cents, job.currency)} />
                  <Badge label={offer.status} />
                </Row>
                {offer.message !== "" && <Body>{offer.message}</Body>}
                <Row gap={space.sm}>
                  {offer.status === "pending" && (
                    <View style={{ flex: 1 }}>
                      <Button
                        label="Accept & book"
                        variant="accent"
                        onPress={run(() => api.acceptOffer(offer.id))}
                        loading={busy}
                      />
                    </View>
                  )}
                  <Button
                    label="Chat"
                    icon="💬"
                    variant="secondary"
                    size="sm"
                    full={false}
                    onPress={() =>
                      navigation.navigate("Chat", { jobId: job.id, workerId: offer.worker_id })
                    }
                  />
                </Row>
              </Card>
            </FadeIn>
          ))}
        </>
      )}

      {/* Worker: send an offer */}
      {mode === "worker" && !isCustomer && job.status === "open" && myOffer == null && (
        <FadeIn delay={60}>
          <Card raised>
            <Title>Send an offer</Title>
            <Subtext>Customers usually book within a few hours.</Subtext>
            <Input
              label="Your price"
              prefix="$"
              value={offerPrice}
              onChangeText={setOfferPrice}
              keyboardType="decimal-pad"
              placeholder="110.00"
            />
            <Input
              label="Message"
              hint="A short note wins more jobs than a low price."
              value={offerMessage}
              onChangeText={setOfferMessage}
              placeholder="I can start tomorrow at 9am."
              autoCapitalize="sentences"
            />
            <Button
              label="Send offer"
              variant="accent"
              loading={busy}
              onPress={run(async () => {
                const cents = Math.round(parseFloat(offerPrice) * 100);
                if (!Number.isFinite(cents) || cents <= 0)
                  throw new ApiError(0, "Enter a valid price");
                await api.makeOffer(job.id, cents, offerMessage.trim());
              })}
            />
          </Card>
        </FadeIn>
      )}

      {myOffer != null && !isCustomer && (
        <Card>
          <Row between>
            <View>
              <Caption>YOUR OFFER</Caption>
              <Price value={money(myOffer.price_cents, job.currency)} />
            </View>
            <Badge label={myOffer.status} />
          </Row>
          <Button
            label="Chat with customer"
            icon="💬"
            variant="secondary"
            onPress={() =>
              navigation.navigate("Chat", { jobId: job.id, workerId: myOffer.worker_id })
            }
          />
        </Card>
      )}

      {/* Lifecycle actions */}
      {(isCustomer || isAssignedWorker) && (
        <Row gap={space.sm}>
          {isAssignedWorker && job.status === "assigned" && (
            <View style={{ flex: 1 }}>
              <Button
                label="Start job"
                icon="▶️"
                variant="accent"
                onPress={run(() => api.startJob(job.id))}
                loading={busy}
              />
            </View>
          )}
          {job.status === "in_progress" && (
            <View style={{ flex: 1 }}>
              <Button
                label="Mark completed"
                icon="✅"
                onPress={run(() => api.completeJob(job.id))}
                loading={busy}
              />
            </View>
          )}
          {job.assigned_worker_id != null && (
            <Button
              label="Chat"
              icon="💬"
              variant="secondary"
              full={false}
              onPress={() =>
                navigation.navigate("Chat", { jobId: job.id, workerId: job.assigned_worker_id! })
              }
            />
          )}
        </Row>
      )}
      {((isCustomer && (job.status === "open" || job.status === "assigned")) ||
        (isAssignedWorker && job.status === "assigned")) && (
        <Button
          label="Cancel job"
          variant="danger"
          onPress={run(() => api.cancelJob(job.id))}
          loading={busy}
        />
      )}

      {/* Ratings */}
      {canRate && (
        <FadeIn>
          <Card raised>
            <Title>Rate this {isCustomer ? "pro" : "customer"}</Title>
            <Subtext>Hidden from them until you've both rated.</Subtext>
            <Row gap={space.xs}>
              {[1, 2, 3, 4, 5].map((n) => (
                <Button
                  key={n}
                  label={n <= stars ? "★" : "☆"}
                  variant={n <= stars ? "accent" : "secondary"}
                  size="sm"
                  full={false}
                  onPress={() => setStars(n)}
                />
              ))}
            </Row>
            <Input
              label="Comment"
              value={comment}
              onChangeText={setComment}
              placeholder="How did it go?"
              autoCapitalize="sentences"
            />
            <Button
              label="Submit rating"
              loading={busy}
              onPress={run(() => api.rateJob(job.id, stars, comment.trim()))}
            />
          </Card>
        </FadeIn>
      )}

      {ratings?.mine != null && (
        <Card>
          <Caption>YOU RATED</Caption>
          <Title>{"★".repeat(ratings.mine.stars)}</Title>
          {ratings.other != null ? (
            <>
              <Caption>THEY RATED YOU</Caption>
              <Title>{"★".repeat(ratings.other.stars)}</Title>
              {ratings.other.comment ? <Body>“{ratings.other.comment}”</Body> : null}
            </>
          ) : (
            <Subtext>
              {ratings.other_submitted
                ? "Their rating unlocks once you've both rated."
                : "Waiting on their rating."}
            </Subtext>
          )}
        </Card>
      )}

      {/* Disputes */}
      {dispute != null && (
        <FadeIn>
          <Card>
            <Row between>
              <Title>{dispute.status === "open" ? "Dispute under review" : "Dispute resolved"}</Title>
              <Badge
                label={dispute.status === "open" ? "in review" : (dispute.outcome ?? "resolved")}
                tone={dispute.status === "open" ? "warning" : "positive"}
              />
            </Row>
            <Caption>{dispute.reason.replace(/_/g, " ").toUpperCase()}</Caption>
            {dispute.detail !== "" && <Body>{dispute.detail}</Body>}
            {dispute.status === "open" ? (
              <Subtext>Our team is reviewing. We'll notify you both when it's settled.</Subtext>
            ) : (
              <>
                {dispute.resolution_note !== "" && <Body>{dispute.resolution_note}</Body>}
                {dispute.refunded_cents > 0 && (
                  <Subtext>
                    Refunded {money(dispute.refunded_cents, job.currency)} to the customer.
                  </Subtext>
                )}
              </>
            )}
          </Card>
        </FadeIn>
      )}

      {(isCustomer || isAssignedWorker) &&
        dispute == null &&
        DISPUTABLE.has(job.status) &&
        (disputeOpen ? (
          <FadeIn>
            <Card raised>
              <Title>Report a problem</Title>
              <Subtext>
                Use this only if something genuinely went wrong — try the chat first.
              </Subtext>
              <Row gap={space.sm}>
                {DISPUTE_REASONS.map((r) => (
                  <Button
                    key={r.value}
                    label={r.label}
                    size="sm"
                    full={false}
                    variant={disputeReason === r.value ? "primary" : "secondary"}
                    onPress={() => setDisputeReason(r.value)}
                  />
                ))}
              </Row>
              <Input
                label="What happened?"
                value={disputeDetail}
                onChangeText={setDisputeDetail}
                placeholder="Give us the details — dates, what was agreed, what went wrong."
                multiline
                numberOfLines={3}
                autoCapitalize="sentences"
              />
              <Button
                label="Submit report"
                variant="danger"
                loading={busy}
                onPress={run(async () => {
                  await api.openDispute(job.id, disputeReason, disputeDetail.trim());
                  setDisputeOpen(false);
                  setDisputeDetail("");
                })}
              />
              <Button label="Never mind" variant="ghost" onPress={() => setDisputeOpen(false)} />
            </Card>
          </FadeIn>
        ) : (
          <Button
            label="Report a problem"
            variant="ghost"
            size="sm"
            onPress={() => setDisputeOpen(true)}
          />
        ))}

      <ErrorText message={error} />
    </Screen>
  );
}

const DISPUTABLE = new Set(["assigned", "in_progress", "completed"]);

const DISPUTE_REASONS: { value: DisputeReason; label: string }[] = [
  { value: "work_not_done", label: "Not done" },
  { value: "quality", label: "Quality" },
  { value: "damage", label: "Damage" },
  { value: "no_show", label: "No-show" },
  { value: "overcharged", label: "Overcharged" },
  { value: "unsafe", label: "Unsafe" },
  { value: "other", label: "Other" },
];

const styles = {
  tradeIcon: {
    width: 42,
    height: 42,
    borderRadius: 13,
    backgroundColor: palette.amberSoft,
    alignItems: "center" as const,
    justifyContent: "center" as const,
  },
};
